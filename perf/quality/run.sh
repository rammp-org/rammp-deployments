#!/bin/bash
# run.sh IMAGE CMD... : run CMD in IMAGE with the dev repo, models, engines and data mounted, as the host user.
img=$1; shift
D=$HOME/nanoowl-dev
exec docker run --rm --runtime nvidia --network host --ipc host --user 1000:1000 -e HOME=/tmp -e OPENBLAS_NUM_THREADS=1 \
  -v $HOME/detector-bench:/bench:ro -v rammp_perception_engines:/cache/engines:ro -v $D/repo:/repo:ro -v $D/scripts:/scripts:ro -v $D/models:/models/owlvit:ro -v $D/engines:/engines -v $D/data:/data \
  -e PYTHONPATH=/repo/object_detection/nanoowl/rammp_nanoowl:/repo/object_detection/common/rammp_detection_common:/repo/object_detection/yoloe/rammp_yoloe:/repo/object_detection/rfdetr/rammp_rfdetr \
  --entrypoint "$1" "$img" "${@:2}"
