#!/usr/bin/env python3
"""One-time export of the YOLOv8n COCO-pretrained detector to ONNX, at both
320 and 640 input resolutions.

Dev-machine-only: needs `ultralytics` (pulls in PyTorch), so it lives in
its own throwaway venv and is never installed on the Pi. The Pi only needs
the resulting .onnx files plus the lightweight `onnxruntime` package.

Usage (from repo root):
    python3 -m venv .venv-export
    source .venv-export/bin/activate
    pip install ultralytics onnx
    python3 scripts/export_model.py
"""
import shutil
from pathlib import Path

from ultralytics import YOLO

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / 'src' / 'detector' / 'models'
RESOLUTIONS = (320, 640)


def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for imgsz in RESOLUTIONS:
        model = YOLO('yolov8n.pt')
        exported_path = Path(model.export(format='onnx', imgsz=imgsz, opset=12, simplify=True))
        dest = MODELS_DIR / f'yolov8n_{imgsz}.onnx'
        shutil.move(str(exported_path), str(dest))
        print(f'Wrote {dest}')


if __name__ == '__main__':
    main()
