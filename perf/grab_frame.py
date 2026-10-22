"""Save one scene camera frame as a JPEG (to check what the bench camera sees)."""
import sys

import cv2
import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

rclpy.init()
node = rclpy.create_node("grab_frame")
got = []
node.create_subscription(Image, "/scene_camera/color/image_raw", got.append, qos_profile_sensor_data)
while not got:
    rclpy.spin_once(node, timeout_sec=1)
m = got[0]
rgb = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, 3)
cv2.imwrite(sys.argv[1], cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
