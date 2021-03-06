# IV. Custom Tryon Datasets

The VVT dataset only features upper clothing for tryon. You may want collect your own 
data for more expansive tryon.

## Option 1: Follow Our Folder Layout
The `VVTDataset` expects the directory structure to look like this:
```
DATASET_ROOT/
    - clothes_person
        - img                    (images of the target user and garment) 
            - PERSON_IDS...
        - keypoint               (we do not use these) 
            - PERSON_IDS...
        - parsing                (we do not use these) 
            - PERSON_IDS...
    - train
        - cloth                  (warped cloths, generated by WarpModule)
            - PERSON_IDs...
        - densepose              (generated by detectron2)
            - PERSON_IDs...
        - optical_flow           (generated by flownet2)
            - PERSON_IDs...
        - train_frames           (original video frames)
            - PERSON_IDs...
        - train_frames_keypoint  (cocopose annotations, optional if using densepose)
            - PERSON_IDs...
        - train_frames_parsing   (LIP annotations)
            - PERSON_IDs...
    - test 
        - cloth                  (warped cloths, generated by WarpModule)
            - PERSON_IDs...
        - densepose              (generated by detectron2)
            - PERSON_IDs...
        - optical_flow           (generated by flownet2)
            - PERSON_IDs...
        - train_frames           (original video frames)
            - PERSON_IDs...
        - train_frames_keypoint  (cocopose annotations, optional if using densepose)
            - PERSON_IDs...
        - train_frames_parsing   (LIP annotations)
            - PERSON_IDs...
```
Each folder should contain subfolders for the video of each person (PERSON_ID).
Each subfolder contains the files that represent the data of each video frame.

The data under corresponding PERSON_IDs should match 1-to-1. 
For example: `cloth/ABC/` should have the same number of files as `densepose/ABC/`.

## Option 2: Custom Folder Layout
If you want to define your own folder layout, you can extend our `TryonDataset` class 
in `datasets/tryon_datasets.py` and override the `@abstractmethod`s that fetch each 
input path. 

See `VVTDataset` as a reference.