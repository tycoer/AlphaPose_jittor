
import jittor as jt
from jittor import init
from jittor import nn
import os
import sys
from threading import Thread
from queue import Queue
import cv2
import numpy as np
from alphapose.utils.presets import SimpleTransform, SimpleTransform3DSMPL
from alphapose.models import builder

class DetectionLoaderST():
    def __init__(self, input_source, detector, cfg, opt, mode='image', batchSize=1, queueSize=128):
        self.cfg = cfg
        self.opt = opt
        self.mode = mode
        # self.device = opt.device
        if (mode == 'image'):
            self.img_dir = opt.inputpath
            self.imglist = [os.path.join(self.img_dir, im_name.rstrip('\n').rstrip('\r')) for im_name in input_source]
            self.datalen = len(input_source)
        elif (mode == 'video'):
            stream = cv2.VideoCapture(input_source)
            assert stream.isOpened(), 'Cannot capture source'
            self.path = input_source
            self.datalen = int(stream.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fourcc = int(stream.get(cv2.CAP_PROP_FOURCC))
            self.fps = stream.get(cv2.CAP_PROP_FPS)
            self.frameSize = (int(stream.get(cv2.CAP_PROP_FRAME_WIDTH)), int(stream.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            self.videoinfo = {'fourcc': self.fourcc, 'fps': self.fps, 'frameSize': self.frameSize}
            stream.release()
        self.detector = detector
        self.batchSize = batchSize
        leftover = 0
        if (self.datalen % batchSize):
            leftover = 1
        self.num_batches = ((self.datalen // batchSize) + leftover)
        self._input_size = cfg.DATA_PRESET.IMAGE_SIZE
        self._output_size = cfg.DATA_PRESET.HEATMAP_SIZE
        self._sigma = cfg.DATA_PRESET.SIGMA
        if (cfg.DATA_PRESET.TYPE == 'simple'):
            pose_dataset = builder.retrieve_dataset(self.cfg.DATASET.TRAIN)
            self.transformation = SimpleTransform(pose_dataset, scale_factor=0, input_size=self._input_size, output_size=self._output_size, rot=0, sigma=self._sigma, train=False, add_dpg=False,
                                                  gpu_device=None
                                                  # gpu_device=self.device
                                                  )
        elif (cfg.DATA_PRESET.TYPE == 'simple_smpl'):
            from easydict import EasyDict as edict
            dummpy_set = edict({'joint_pairs_17': None, 'joint_pairs_24': None, 'joint_pairs_29': None, 'bbox_3d_shape': (2.2, 2.2, 2.2)})
            self.transformation = SimpleTransform3DSMPL(dummpy_set, scale_factor=cfg.DATASET.SCALE_FACTOR, color_factor=cfg.DATASET.COLOR_FACTOR, occlusion=cfg.DATASET.OCCLUSION, input_size=cfg.MODEL.IMAGE_SIZE, output_size=cfg.MODEL.HEATMAP_SIZE, depth_dim=cfg.MODEL.EXTRA.DEPTH_DIM, bbox_3d_shape=(2.2, 2.2, 2.2), rot=cfg.DATASET.ROT_FACTOR, sigma=cfg.MODEL.EXTRA.SIGMA, train=False, add_dpg=False, loss_type=cfg.LOSS['TYPE'])

    def image_preprocess(self):
        for i in range(self.num_batches):
            imgs = []
            orig_imgs = []
            im_names = []
            im_dim_list = []
            for k in range((i * self.batchSize), min(((i + 1) * self.batchSize), self.datalen)):
                im_name_k = self.imglist[k]
                img_k = self.detector.image_preprocess(im_name_k)
                if isinstance(img_k, np.ndarray):
                    img_k = jt.array(img_k)
                if (img_k.ndim == 3):
                    img_k = img_k.unsqueeze(0)
                orig_img_k = cv2.cvtColor(cv2.imread(im_name_k), cv2.COLOR_BGR2RGB)
                im_dim_list_k = (orig_img_k.shape[1], orig_img_k.shape[0])
                imgs.append(img_k)
                orig_imgs.append(orig_img_k)
                im_names.append(os.path.basename(im_name_k))
                im_dim_list.append(im_dim_list_k)
            with jt.no_grad():
                imgs = jt.concat(imgs)
                im_dim_list = jt.float32(im_dim_list).repeat(1, 2)

    def read(self):
        stream = cv2.VideoCapture(self.path)
        assert stream.isOpened(), 'Cannot capture source'

        for i in range(self.num_batches):
            imgs = []
            orig_imgs = []
            im_names = []
            im_dim_list = []
            for k in range((i * self.batchSize), min(((i + 1) * self.batchSize), self.datalen)):
                (grabbed, frame) = stream.read()
                if (not grabbed):
                    if (len(imgs) > 0):
                        with jt.no_grad():
                            imgs = jt.contrib.concat(imgs)
                            im_dim_list = jt.float32(im_dim_list).repeat(1, 2)
                img_k = self.detector.image_preprocess(frame)
                if isinstance(img_k, np.ndarray):
                    img_k = jt.array(img_k)
                if (img_k.ndim == 3):
                    img_k = img_k.unsqueeze(0)
                im_dim_list_k = (frame.shape[1], frame.shape[0])
                imgs.append(img_k)
                orig_imgs.append(frame[:, :, ::(- 1)])
                im_names.append((str(k) + '.jpg'))
                im_dim_list.append(im_dim_list_k)
            with jt.no_grad():
                imgs = jt.contrib.concat(imgs)
                im_dim_list = jt.float32(im_dim_list).repeat(1, 2)
                detection_res = self.image_detection_wit_single_batch(imgs, orig_imgs, im_names, im_dim_list)
                postprocess_res = self.image_postprocess_with_single_batch(*detection_res)

        stream.release()

    def image_detection_wit_single_batch(self, imgs, orig_imgs, im_names, im_dim_list):
        if ((imgs is None)):
            return (None, None, None, None, None, None, None)
        with jt.no_grad():
            for pad_i in range((self.batchSize - len(imgs))):
                imgs = jt.contrib.concat((imgs, jt.unsqueeze(imgs[0], dim=0)), dim=0)
                im_dim_list = jt.contrib.concat((im_dim_list, jt.unsqueeze(im_dim_list[0], dim=0)), dim=0)
            dets = self.detector.images_detection(imgs, im_dim_list)
            if (isinstance(dets, int) or (dets.shape[0] == 0)):
                for k in range(len(orig_imgs)):
                    return (orig_imgs[k], im_names[k], None, None, None, None, None)
            if isinstance(dets, np.ndarray):
                dets = jt.array(dets)
            boxes = dets[:, 1:5]
            scores = dets[:, 5:6]
            if self.opt.tracking:
                ids = dets[:, 6:7]
            else:
                ids = jt.zeros(scores.shape)
        for k in range(len(orig_imgs)):
            boxes_k = boxes[(dets[:, 0] == k)]
            if (isinstance(boxes_k, int) or (boxes_k.shape[0] == 0)):
                return (orig_imgs[k], im_names[k], None, None, None, None, None)
            inps = jt.zeros((boxes_k.shape[0], 3, *self._input_size))
            cropped_boxes = jt.zeros((boxes_k.shape[0], 4))
            return (orig_imgs[k], im_names[k], boxes_k, scores[(dets[:, 0] == k)], ids[(dets[:, 0] == k)], inps, cropped_boxes)

    def image_postprocess_with_single_batch(self, orig_img, im_name, boxes, scores, ids, inps, cropped_boxes):
        for i in range(self.datalen):
            with jt.no_grad():
                (orig_img, im_name, boxes, scores, ids, inps, cropped_boxes) = self.wait_and_get(self.det_queue)
                if ((orig_img is None) or self.stopped):
                    self.wait_and_put(self.pose_queue, (None, None, None, None, None, None, None))
                    return
                if ((boxes is None) or (len(boxes) == 0)):
                    self.wait_and_put(self.pose_queue, (None, orig_img, im_name, boxes, scores, ids, None))
                    continue
                for (i, box) in enumerate(boxes):
                    (inps[i], cropped_box) = self.transformation.test_transform(orig_img, box)
                    cropped_boxes[i] = jt.float32(cropped_box)
                self.wait_and_put(self.pose_queue, (inps, orig_img, im_name, boxes, scores, ids, cropped_boxes))


    @property
    def length(self):
        return self.datalen