"""Record the scene camera, and replay it where there is none (snorlax, before its cameras are cabled).

  record_camera  python3 replay_camera.py record out.npz [--frames 90]
                 color + registered depth pairs (matched by stamp), both camera_infos, and the camera's static
                 TF from scene_camera_link to the color frame.
  replay         python3 replay_camera.py replay out.npz
                 publishes them in a loop at 30 Hz on the Gemini's topics with fresh stamps, plus that TF.

What the replay does not reproduce: the Gemini driver's own CPU (~0.6 cores on abra) and its point cloud.
"""

import argparse
import time

import numpy as np
import rclpy
from geometry_msgs.msg import TransformStamped
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Header
from tf2_ros import Buffer, StaticTransformBroadcaster, TransformListener

NS = "/scene_camera"
INFO_FIELDS = ("height", "width", "distortion_model", "d", "k", "r", "p")


def stamp(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


def record(path, n):
    node = rclpy.create_node("record_camera")
    colors, depths, infos = {}, {}, {}
    node.create_subscription(Image, NS + "/color/image_raw", lambda m: colors.setdefault(stamp(m), m), qos_profile_sensor_data)
    node.create_subscription(Image, NS + "/depth/image_raw", lambda m: depths.setdefault(stamp(m), m), qos_profile_sensor_data)
    for k in ("color", "depth"):
        node.create_subscription(CameraInfo, NS + "/%s/camera_info" % k, lambda m, k=k: infos.setdefault(k, m), qos_profile_sensor_data)
    tf = Buffer()
    TransformListener(tf, node)
    def pairs():  # color stamp -> depth stamp, nearest within 20 ms (the two streams are stamped separately)
        out = {}
        for c in sorted(colors):
            d = min(depths, key=lambda d: abs(d - c), default=None)
            if d is not None and abs(d - c) < 0.02:
                out[c] = d
        return out

    while len(pairs()) < n or len(infos) < 2:
        rclpy.spin_once(node, timeout_sec=0.1)
    matched = pairs()
    keys = sorted(matched)[:n]
    c0 = colors[keys[0]]
    t = tf.lookup_transform("scene_camera_link", c0.header.frame_id, rclpy.time.Time(), timeout=rclpy.duration.Duration(seconds=5))
    tr, q = t.transform.translation, t.transform.rotation
    out = {
        "color": np.stack([np.frombuffer(colors[k].data, np.uint8).reshape(c0.height, c0.width, 3) for k in keys]),
        "depth": np.stack([np.frombuffer(depths[matched[k]].data, np.uint16).reshape(c0.height, c0.width) for k in keys]),
        "color_encoding": c0.encoding, "depth_encoding": depths[matched[keys[0]]].encoding,
        "color_frame": c0.header.frame_id, "depth_frame": depths[matched[keys[0]]].header.frame_id,
        "tf": np.array([tr.x, tr.y, tr.z, q.x, q.y, q.z, q.w]),
    }
    for k, m in infos.items():
        out["%s_info" % k] = np.array(repr({f: list(getattr(m, f)) if f in "dkrp" else getattr(m, f) for f in INFO_FIELDS}))
    np.savez(path, **out)
    print("recorded %d frames %dx%d (%s) -> %s" % (n, c0.width, c0.height, c0.encoding, path))


def replay(path, hz):
    d = np.load(path)
    node = rclpy.create_node("scene_camera_replay")
    pub_c = node.create_publisher(Image, NS + "/color/image_raw", qos_profile_sensor_data)
    pub_d = node.create_publisher(Image, NS + "/depth/image_raw", qos_profile_sensor_data)
    pub_ic = node.create_publisher(CameraInfo, NS + "/color/camera_info", qos_profile_sensor_data)
    pub_id = node.create_publisher(CameraInfo, NS + "/depth/camera_info", qos_profile_sensor_data)
    infos = {}
    for k in ("color", "depth"):
        f = eval(str(d["%s_info" % k]))  # our own repr from record()
        infos[k] = CameraInfo(**f)
        infos[k].header.frame_id = str(d["%s_frame" % k])

    t = TransformStamped()
    t.header.stamp = node.get_clock().now().to_msg()
    t.header.frame_id, t.child_frame_id = "scene_camera_link", str(d["color_frame"])
    v = d["tf"]
    t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = map(float, v[:3])
    t.transform.rotation.x, t.transform.rotation.y, t.transform.rotation.z, t.transform.rotation.w = map(float, v[3:])
    StaticTransformBroadcaster(node).sendTransform(t)

    color, depth = d["color"], d["depth"]
    n, h, w = color.shape[:3]
    frames = [(color[i].tobytes(), depth[i].tobytes()) for i in range(n)]
    print("replaying %d frames %dx%d at %.0f Hz" % (n, w, h, hz), flush=True)
    period, next_t, i = 1.0 / hz, time.monotonic(), 0
    while rclpy.ok():
        now = node.get_clock().now().to_msg()
        c, z = frames[i % n]
        pub_c.publish(Image(height=h, width=w, encoding=str(d["color_encoding"]), step=w * 3, data=c,
                            header=Header(stamp=now, frame_id=str(d["color_frame"]))))
        pub_d.publish(Image(height=h, width=w, encoding=str(d["depth_encoding"]), step=w * 2, data=z,
                            header=Header(stamp=now, frame_id=str(d["depth_frame"]))))
        for k, pub in (("color", pub_ic), ("depth", pub_id)):
            infos[k].header.stamp = now
            pub.publish(infos[k])
        rclpy.spin_once(node, timeout_sec=0)
        i += 1
        next_t += period
        time.sleep(max(0.0, next_t - time.monotonic()))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["record", "replay"])
    p.add_argument("path")
    p.add_argument("--frames", type=int, default=60)
    p.add_argument("--hz", type=float, default=30.0)
    a = p.parse_args()
    rclpy.init()
    record(a.path, a.frames) if a.mode == "record" else replay(a.path, a.hz)


if __name__ == "__main__":
    main()
