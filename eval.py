from utils.getter import *
import argparse
import os
import cv2
import matplotlib.pyplot as plt 
import json
import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader
from utils.utils import box_nms_numpy, draw_boxes_v2, change_box_order
import pandas as pd
from tqdm import tqdm
import albumentations as A
from albumentations.pytorch.transforms import ToTensorV2

BATCH_SIZE = 4

class TestDataset(Dataset):
    def __init__(self, config, test_df, transforms=None):
        self.image_size = config.image_size
        self.root_dir = os.path.join('datasets', config.project_name, config.test_imgs)
        self.test_df = test_df
        self.transforms = transforms
        self.load_data()

    def load_data(self):
        self.fns = [
            annotations for annotations in zip(
                self.test_df['image_id'], self.test_df['width'], self.test_df['height']
            )
        ]

    def __getitem__(self, idx):
        image_id, image_w, image_h = self.fns[idx]
        img_path = os.path.join(self.root_dir, image_id+'.png')
        img = cv2.imread(img_path)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
        img /= 255.0
        img_ = cv2.resize(img, tuple(self.image_size))

        if self.transforms is not None:
            img = self.transforms(image=img_)['image']
        return {
            'ori_image': img,
            'image_id': image_id,
            'img': img,
            'image_w': image_w,
            'image_h': image_h
        }

    def collate_fn(self, batch):
        imgs = torch.stack([s['img'] for s in batch])   
        img_ids = [s['image_id'] for s in batch]
        ori_imgs = [s['ori_image'] for s in batch]
        img_ws = [s['image_w'] for s in batch]
        img_hs = [s['image_h'] for s in batch]

        return {
            'imgs': imgs,
            'ori_imgs': ori_imgs,
            'img_ids': img_ids,
            'img_ws': img_ws,
            'img_hs': img_hs
        }
    def __len__(self):
      return len(self.fns)

def main(args, config):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    if torch.cuda.is_available():
        torch.cuda.manual_seed(42)
    else:
        torch.manual_seed(42)

    if args.output_path is not None:
        if not os.path.exists(args.output_path):
            os.makedirs(args.output_path)

    test_df = pd.read_csv('/content/main/datasets/test_info.csv')
    test_transforms = A.Compose([
        A.Resize(
            height = config.image_size[1],
            width = config.image_size[0]),
        ToTensorV2(p=1.0)
    ])

    testset = TestDataset(config, test_df, test_transforms)
    testloader = DataLoader(testset, batch_size = BATCH_SIZE, collate_fn=testset.collate_fn)
    idx_classes = {idx:i for idx,i in enumerate(config.obj_list)}
    NUM_CLASSES = len(config.obj_list)
    net = EfficientDetBackbone(num_classes=NUM_CLASSES, compound_coef=args.c,
                                 ratios=eval(config.anchors_ratios), scales=eval(config.anchors_scales))

    model = Detector(
                    n_classes=NUM_CLASSES,
                    model = net,
                    criterion= FocalLoss(), 
                    optimizer= torch.optim.Adam,
                    optim_params = {'lr': 0.1},     
                    device = device)
    model.eval()
    if args.weight is not None:                
        load_checkpoint(model, args.weight)
    
    results = []
    empty_imgs = 0
    with tqdm(total=len(testloader)) as pbar:
        with torch.no_grad():
            for idx, batch in enumerate(testloader):
                preds = model.inference_step(batch, args.min_conf, args.min_iou)
                for idx, outputs in enumerate(preds):
                    img_id = batch['img_ids'][idx]
                    ori_img = batch['ori_imgs'][idx]
                    img_w = batch['img_ws'][idx]
                    img_h = batch['img_hs'][idx]
                    
                    boxes = outputs['bboxes'] 
                    labels = outputs['classes']  
                    scores = outputs['scores']
                    if len(boxes) == 0:
                        empty_imgs += 1
                        boxes = None
                    else:
                        boxes = change_box_order(boxes, order='xyxy2xywh')
                        #boxes, scores, labels = box_nms_numpy(boxes, scores, labels, threshold=0.15, box_format='xywh')
                    if boxes is not None:
                        if args.output_path is not None:
                            out_path = os.path.join(args.output_path, f'{img_id}.png')
                            draw_boxes_v2(out_path, ori_img , boxes, labels, scores, idx_classes)

                    if args.submission:
                        if boxes is not None:
                            pred_strs = []
                            for box, score, cls_id in zip(boxes, scores, labels):
                                x,y,w,h = box

                                x = round(float(x*1.0*img_w/config.image_size[0]))
                                y = round(float(y*1.0*img_h/config.image_size[1]))
                                w = round(float(w*1.0*img_w/config.image_size[0]))
                                h = round(float(h*1.0*img_h/config.image_size[1]))
                                score = np.round(float(score),2)
                                pred_strs.append(f'{cls_id} {score} {x} {y} {x+w} {y+h}')
                            pred_str = ' '.join(pred_strs)
                        else:
                            pred_str = '14 1 0 0 1 1'
                
                    results.append([img_id, pred_str])
                pbar.update(BATCH_SIZE)
                pbar.set_description(f'Empty images: {empty_imgs}')

        if args.submission:
            submission_df = pd.DataFrame(results, columns=['image_id', 'PredictionString'])
            submission_df.to_csv('results/submission.csv', index=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Inference AIC Challenge Dataset')
    parser.add_argument('--min_conf', type=float, default= 0.3, help='minimum confidence for an object to be detect')
    parser.add_argument('--min_iou', type=float, default=0.4, help='minimum iou threshold for non max suppression')
    parser.add_argument('-c', type=int, default = 2, help='version of EfficentDet')
    parser.add_argument('--weight', type=str, default = 'weights/efficientdet-d2.pth',help='version of EfficentDet')
    parser.add_argument('--output_path', type=str, default = None, help='name of output to .avi file')
    parser.add_argument('--submission', action='store_true', default = False, help='output to submission file')
    parser.add_argument('--config', type=str, default = None,help='save detection at')

    args = parser.parse_args() 
    config = Config(os.path.join('configs',args.config+'.yaml'))                   
    main(args, config)
    