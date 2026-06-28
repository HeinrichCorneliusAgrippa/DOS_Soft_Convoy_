import os
import time
import threading
import warnings
import yaml
import numpy as np
import cv2
from typing import List, Tuple

warnings.filterwarnings("ignore", category=FutureWarning)

from tasks.object_detection.packages import integration_activity as student

_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'config', 'object_detection_config.yaml'
))

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

CLASS_NAMES  = {0: 'duckie', 1: 'truck', 2: 'sign'}
CLASS_COLORS = {0: (0, 215, 255), 1: (180, 100, 220), 2: (50, 205, 50)}

Detection = Tuple[Tuple[int, int, int, int], float, int]


def _xywh2xyxy(cx, cy, w, h, model_size, img_w, img_h):
    sx = img_w / model_size
    sy = img_h / model_size
    x1 = int((cx - w / 2) * sx)
    y1 = int((cy - h / 2) * sy)
    x2 = int((cx + w / 2) * sx)
    y2 = int((cy + h / 2) * sy)
    return max(0, x1), max(0, y1), min(img_w - 1, x2), min(img_h - 1, y2)


class ObjectDetectionAgent:

    def __init__(self, config_path: str = None):
        path = config_path or _CONFIG_FILE
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}

        self.img_size       = cfg.get('img_size',       416)
        self.conf_threshold = cfg.get('conf_threshold', 0.5)
        self.nms_threshold  = cfg.get('nms_threshold',  0.45)

        self.model_path       = self._resolve_model_path(student.MODEL_PATH)
        self.frame_count      = 0
        self.session          = None
        self.net              = None
        self._trt_context     = None
        self._backend         = None
        self.model_loaded     = False
        self.load_error       = None
        self.trt_building     = False
        self._trt_build_start = None

        self._load_model()

    @staticmethod
    def _resolve_model_path(model_path: str) -> str:
        if os.path.isabs(model_path):
            return model_path
        return os.path.normpath(os.path.join(_PROJECT_ROOT, model_path))

    def _load_model(self):
        if not os.path.isfile(self.model_path):
            self.load_error = f"Model file not found: {self.model_path}"
            print(f"[ObjectDetection] {self.load_error}")
            return

        if self._tensorrt_available():
            self.trt_building     = True
            self._trt_build_start = time.time()
            threading.Thread(target=self._build_trt_engine, daemon=True).start()
        elif self._try_onnxruntime():
            pass
        else:
            self._try_cv2dnn()

    def _tensorrt_available(self):
        try:
            import tensorrt  # noqa: F401
            import ctypes
            ctypes.CDLL('libcudart.so')
            return True
        except (ImportError, OSError):
            return False

    def _build_trt_engine(self):
        """Compiles ONNX to TensorRT engine in memory and sets up inference buffers."""
        try:
            import tensorrt as trt
            import ctypes
            cudart = ctypes.CDLL('libcudart.so')

            print("[ObjectDetection] Compiling TensorRT engine (~1 min)...")
            logger  = trt.Logger(trt.Logger.WARNING)
            builder = trt.Builder(logger)
            network = builder.create_network(
                1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
            )
            parser = trt.OnnxParser(network, logger)
            with open(self.model_path, 'rb') as f:
                if not parser.parse(f.read()):
                    for i in range(parser.num_errors):
                        print(f"[ObjectDetection] TRT parse error: {parser.get_error(i)}")
                    self.load_error = "TRT ONNX parse failed"
                    return

            config = builder.create_builder_config()
            config.max_workspace_size = 1 << 28
            engine = builder.build_engine(network, config)
            if engine is None:
                self.load_error = "TRT build returned None"
                print(f"[ObjectDetection] {self.load_error}")
                return

            context = engine.create_execution_context()
            host_in, host_out, dev_ptrs = [], [], []
            for binding in engine:
                size  = trt.volume(engine.get_binding_shape(binding))
                dtype = trt.nptype(engine.get_binding_dtype(binding))
                host  = np.empty(size, dtype=dtype)
                dev   = ctypes.c_void_p()
                cudart.cudaMalloc(ctypes.byref(dev), host.nbytes)
                dev_ptrs.append(dev.value)
                if engine.binding_is_input(binding):
                    host_in.append(host)
                else:
                    host_out.append(host)

            self._trt_context   = context
            self._cudart        = cudart
            self._trt_host_in   = host_in
            self._trt_host_out  = host_out
            self._trt_dev_ptrs  = dev_ptrs
            self._trt_out_shape = tuple(engine.get_binding_shape(1))
            self._backend       = 'trt'
            self.img_size       = engine.get_binding_shape(0)[2]
            self.model_loaded   = True
            print(f"[ObjectDetection] TRT output shape: {self._trt_out_shape}")
            print(f"[ObjectDetection] Model ready (TensorRT, img_size={self.img_size}).")
        except Exception as e:
            self.load_error = f"TRT build failed: {e}"
            print(f"[ObjectDetection] {self.load_error}")
        finally:
            self.trt_building = False

    @property
    def trt_build_elapsed(self) -> int:
        if self._trt_build_start is None:
            return 0
        return int(time.time() - self._trt_build_start)

    def _try_onnxruntime(self):
        try:
            import onnxruntime as ort
        except ImportError:
            return False
        try:
            print("[ObjectDetection] Loading ONNX model via onnxruntime...")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = os.cpu_count() or 4
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                self.model_path,
                sess_options=opts,
                providers=['CUDAExecutionProvider', 'CPUExecutionProvider'],
            )
            inp = self.session.get_inputs()[0]
            self._input_name  = inp.name
            self._output_name = self.session.get_outputs()[0].name
            self.img_size     = inp.shape[2]
            self._backend     = 'ort'
            self.model_loaded = True
            used = self.session.get_providers()[0]
            print(f"[ObjectDetection] Model ready (onnxruntime, provider={used}, img_size={self.img_size}).")
            return True
        except Exception as e:
            print(f"[ObjectDetection] onnxruntime failed ({e}), trying cv2.dnn...")
            return False

    def _try_cv2dnn(self):
        try:
            print("[ObjectDetection] Loading ONNX model via cv2.dnn...")
            self.net          = cv2.dnn.readNetFromONNX(self.model_path)
            self._backend     = 'cv2dnn'
            self.model_loaded = True
            print(f"[ObjectDetection] Model ready (cv2.dnn, img_size={self.img_size}).")
        except Exception as e:
            self.load_error = f"Failed to load ONNX model: {e}"
            print(f"[ObjectDetection] {self.load_error}")

    def _frame_skip(self) -> int:
        try:
            return max(0, int(student.NUMBER_FRAMES_SKIPPED()))
        except Exception:
            return 0

    def _preprocess(self, frame_rgb: np.ndarray) -> np.ndarray:
        img = cv2.resize(frame_rgb, (self.img_size, self.img_size))
        img = img.astype(np.float32) / 255.0
        img = img.transpose(2, 0, 1)
        return np.ascontiguousarray(img)

    def _postprocess(self, raw: np.ndarray, orig_w: int, orig_h: int) -> List[Detection]:
        predictions = raw[0]
        n_cols = predictions.shape[1]

        if n_cols == 6:
            # NMS-included export: [x1, y1, x2, y2, conf, cls_id] already in img_size pixel space
            return self._postprocess_xyxy(predictions, orig_w, orig_h)

        # Raw YOLOv5: [cx, cy, w, h, obj_conf, cls0, cls1, ...] in img_size pixel space
        obj_conf     = predictions[:, 4]
        class_scores = predictions[:, 5:]
        cls_ids      = np.argmax(class_scores, axis=1)
        cls_conf     = class_scores[np.arange(len(class_scores)), cls_ids]
        scores       = obj_conf * cls_conf

        mask        = scores >= self.conf_threshold
        predictions = predictions[mask]
        scores      = scores[mask]
        cls_ids     = cls_ids[mask]

        if len(predictions) == 0:
            return []

        boxes_xywh = predictions[:, :4]
        boxes_cv   = [[int(cx - bw/2), int(cy - bh/2), int(bw), int(bh)]
                      for cx, cy, bw, bh in boxes_xywh]

        indices = cv2.dnn.NMSBoxes(
            boxes_cv, scores.tolist(), self.conf_threshold, self.nms_threshold
        )

        if len(indices) == 0:
            return []

        detections: List[Detection] = []
        for i in indices.flatten():
            cx, cy, bw, bh = boxes_xywh[i]
            bbox   = _xywh2xyxy(cx, cy, bw, bh, self.img_size, orig_w, orig_h)
            cls_id = int(cls_ids[i])
            score  = float(scores[i])

            if not student.filter_by_classes(cls_id):
                continue
            if not student.filter_by_scores(score):
                continue
            if not student.filter_by_bboxes(bbox):
                continue

            detections.append((bbox, score, cls_id))

        return detections

    def _postprocess_xyxy(self, predictions: np.ndarray, orig_w: int, orig_h: int) -> List[Detection]:
        scores  = predictions[:, 4]
        cls_ids = predictions[:, 5].astype(int)

        mask        = scores >= self.conf_threshold
        predictions = predictions[mask]
        scores      = scores[mask]
        cls_ids     = cls_ids[mask]

        if len(predictions) == 0:
            return []

        sx = orig_w / self.img_size
        sy = orig_h / self.img_size

        detections: List[Detection] = []
        for idx, (x1, y1, x2, y2, score, cls_id_f) in enumerate(predictions):
            bbox = (
                max(0, int(x1 * sx)), max(0, int(y1 * sy)),
                min(orig_w - 1, int(x2 * sx)), min(orig_h - 1, int(y2 * sy)),
            )
            cls_id = int(cls_ids[idx])
            score  = float(scores[idx])

            if not student.filter_by_classes(cls_id):
                continue
            if not student.filter_by_scores(score):
                continue
            if not student.filter_by_bboxes(bbox):
                continue

            detections.append((bbox, score, cls_id))

        return detections

    def detect(self, frame_rgb: np.ndarray) -> List[Detection]:
        self.frame_count += 1

        if not self.model_loaded:
            return []

        skip = self._frame_skip()
        if skip > 0 and (self.frame_count % (skip + 1)) != 0:
            return None

        orig_h, orig_w = frame_rgb.shape[:2]
        try:
            raw = self._infer(frame_rgb)
        except Exception as e:
            print(f"[ObjectDetection] Inference error: {e}")
            return None

        if self.frame_count == 1:
            print(f"[ObjectDetection] Raw output shape: {raw.shape}")

        return self._postprocess(raw, orig_w, orig_h)

    def _infer(self, frame_rgb: np.ndarray) -> np.ndarray:
        if self._backend == 'trt':
            import ctypes
            H2D, D2H = 1, 2
            inp = self._preprocess(frame_rgb).flatten()
            np.copyto(self._trt_host_in[0], inp)
            self._cudart.cudaMemcpy(
                ctypes.c_void_p(self._trt_dev_ptrs[0]),
                self._trt_host_in[0].ctypes.data_as(ctypes.c_void_p),
                self._trt_host_in[0].nbytes, H2D,
            )
            self._trt_context.execute_v2(bindings=self._trt_dev_ptrs)
            out = self._trt_host_out[0]
            self._cudart.cudaMemcpy(
                out.ctypes.data_as(ctypes.c_void_p),
                ctypes.c_void_p(self._trt_dev_ptrs[1]),
                out.nbytes, D2H,
            )
            return out.reshape(self._trt_out_shape)

        elif self._backend == 'ort':
            inp = self._preprocess(frame_rgb)[np.newaxis]
            return self.session.run([self._output_name], {self._input_name: inp})[0]

        else:
            blob = cv2.dnn.blobFromImage(
                frame_rgb, 1 / 255.0, (self.img_size, self.img_size), swapRB=False
            )
            self.net.setInput(blob)
            return self.net.forward()
