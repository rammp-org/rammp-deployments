#!/bin/bash
# Build the eval set, the SAM3 reference, and every detector's dump.
set -e
R=$HOME/nanoowl-dev/scripts/run.sh
T=sha-8091bbee8f9a147f2811290b8ea780542ee13df7
$R ghcr.io/rammp-org/rammp-perception/yoloe:dev python3 /scripts/make_evalset.py
~/sam3-test/.venv/bin/python ~/nanoowl-dev/scripts/ref_sam3.py ~/nanoowl-dev/data 2>&1 | grep -v -i warn | tail -25
$R ghcr.io/rammp-org/rammp-perception/yoloe:dev python3 /scripts/dump_detectors.py yoloe-26m 2>&1 | tail -2
$R ghcr.io/rammp-org/rammp-perception/rfdetr:$T python3 /scripts/dump_detectors.py rfdetr-s 2>&1 | tail -2
for m in owlvit-b32 owlvit-b16; do $R ghcr.io/rammp-org/rammp-perception/nanoowl:dev python3 /scripts/dump_detectors.py $m 2>&1 | tail -2; done
