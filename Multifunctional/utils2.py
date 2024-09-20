# -*- coding: utf-8 -*-
from torch.utils.data import Dataset, DataLoader
import os
from skimage import io
from PIL import Image
import torch
import csv
from torch import nn
import torch.nn.functional as F
import torchvision
import pytorch_ssim
import numpy as np
from model.vgg16 import VGG16


class VGGLoss(nn.Module):
    """
    Part of pre-trained VGG16.
    See for instance https://arxiv.org/abs/1603.08155

    block_no：how many blocks do we need; layer_within_block：which layer within the block do we need
    """

    def __init__(self, block_no: int, layer_within_block: int, use_batch_norm_vgg: bool): # (3,1,False)
        super(VGGLoss, self).__init__()
        if use_batch_norm_vgg:
            vgg16 = torchvision.models.vgg16_bn(weights=torchvision.models.VGG16_BN_Weights.IMAGENET1K_V1)
        else:
            vgg16 = torchvision.models.vgg16(weights=torchvision.models.VGG16_Weights.IMAGENET1K_V1)
        curr_block = 1  
        curr_layer = 1 
        layers = []
        for layer in vgg16.features.children():  
            layers.append(layer)
            if curr_block == block_no and curr_layer == layer_within_block:  
                break
            if isinstance(layer, nn.MaxPool2d):   
                curr_block += 1
                curr_layer = 1
            else:
                curr_layer += 1
        self.vgg_loss = nn.Sequential(*layers)
        # print(layers)
    def forward(self, img):
        return self.vgg_loss(img)

class VGGLoss2(nn.Module):
    def __init__(self): 
        super(VGGLoss2, self).__init__()
        vgg16 = VGG16()
        vgg16.load_state_dict(torch.load('VGG16.pth'))
        vgg16.eval()

        self.vgg_loss = nn.Sequential(
            vgg16.features[0],
            vgg16.features[2],
            vgg16.features[3],
            vgg16.features[5],
            vgg16.features[6],
            vgg16.features[7],
            vgg16.features[9],
            vgg16.features[10],
            vgg16.features[12],
            vgg16.features[13],
            vgg16.features[14]

        )
        # print(self.vgg_loss)
    def forward(self, img):
        return self.vgg_loss(img)


def write_losses(file_name, iteration, id, losses, time):
    if not os.path.exists(file_name):
        with open(file_name, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            name = 'epoch_id'
            row_to_write = ['iteration'] + [name] + [loss_name for loss_name in losses.keys()] + ['time']
            writer.writerow(row_to_write)

    with open(file_name, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        row_to_write = [iteration] + [id] + ['{:.8f}'.format(loss_val) for loss_val in losses.values()] + \
                       ['{:.0f}'.format(time)]
        writer.writerow(row_to_write)


def psnr1(image1, image2):
    """each element should be in [0, 1]"""
    mse = torch.mean((image1 - image2) ** 2)
    if mse < 1.0e-10:
        return 100
    PIXEL_MAX = 1.0
    return 20 * torch.log10(PIXEL_MAX / torch.sqrt(mse))


def psnr2(image_batch1, image_batch2):
    """each element should be in [0, 255]"""
    mse = torch.mean((image_batch1 - image_batch2) ** 2)
    if mse < 1.0e-10:
        return 100
    PIXEL_MAX = 255.0
    return 20 * torch.log10(PIXEL_MAX / torch.sqrt(mse))


def psnr_between_batches(batch1, batch2):
    batch_size = batch1.size(0)
    average = 0.
    for sample_id in range(batch_size):
        p = psnr2(batch1[sample_id], batch2[sample_id])
        average += p
    average /= batch_size
    return average


def psnr_between_dirs(dir1, dir2):
    file_names1 = os.listdir(dir1)
    file_names2 = os.listdir(dir2)
    file_count = len(file_names1)
    average = 0.
    for i in range(file_count):
        file1 = os.path.join(dir1, file_names1[i])
        file2 = os.path.join(dir2, file_names2[i])
        img1 = torch.from_numpy(io.imread(file1).astype("float32"))
        img2 = torch.from_numpy(io.imread(file2).astype("float32"))
        p = psnr2(img1, img2)
        average += p
    average /= file_count
    return average


def detector_accuracy(outputs, labels):
    _, argmax = torch.max(outputs, 1)
    return (labels == argmax).float().mean()


def save_images(log_dir, images, iteration, name):
    image_dir = os.path.join(log_dir, "images")
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)
    batch_size = images.size(0)
    filename = os.path.join(image_dir, 'iter_' + str(iteration) + '_' + name + '.png')
    torchvision.utils.save_image(images, filename, nrow=8, padding=0, normalize=True)


def ssim_between_dirs(dir1, dir2, use_gpu=False):
    test_transform = torchvision.transforms.Compose([
        torchvision.transforms.ToTensor()
    ])
    pair_dataset = Pairdataset(dir1, dir2, test_transform)
    pair_loader = DataLoader(pair_dataset, batch_size=2, drop_last=False, shuffle=False)
    average = 0
    for pair in pair_loader:
        if use_gpu:
            covers = pair['cover'].cuda()
            stegos = pair['stego'].cuda()
        p = pytorch_ssim.ssim(covers, stegos)
        average += p
    average /= len(pair_loader)
    return average


class Pairdataset(Dataset):
    def __init__(self, cover_dir, stego_dir, transform=None):
        self.cover_dir = cover_dir
        self.stego_dir = stego_dir
        self.transform = transform
        self.file_names = os.listdir(cover_dir)

    def __len__(self):
        return len(self.file_names)

    def __getitem__(self, idx):
        cover_path = os.path.join(self.cover_dir, self.file_names[idx])
        stego_path = os.path.join(self.stego_dir, self.file_names[idx])
        cover = Image.open(cover_path)
        stego = Image.open(stego_path)
        if self.transform:
            cover = self.transform(cover)
            stego = self.transform(stego)
        else:
            # transforms.ToTensor is not used. let data ∈ {0.0, ... 255.0}
            cover = np.array(cover)
            cover = torch.tensor(cover).permute(2, 0, 1).float()
            stego = np.array(stego)
            stego = torch.tensor(stego).permute(2, 0, 1).float()
        return {'cover': cover, 'stego': stego}


def vgg_between_dirs(img_dir1, img_dir2, use_gpu=False):
    vgg = VGGLoss(3, 1, False)
    mse_loss = torch.nn.MSELoss()
    if use_gpu:
        vgg.cuda()
        mse_loss.cuda()
    pair_dataset = Pairdataset(img_dir1, img_dir2)
    # each item {'cover': (3,256,256)tensor, 'stego':(3,256,256)tensor}
    pair_loader = DataLoader(pair_dataset, batch_size=10, drop_last=False, shuffle=False)
    average = 0
    with torch.no_grad():
        for pair in pair_loader:
            covers = pair['cover'].cuda()
            stegos = pair['stego'].cuda()
            vgg_on_cov = vgg(covers)
            vgg_on_enc = vgg(stegos)
            g_vgg_loss = mse_loss(vgg_on_enc, vgg_on_cov)
            average += g_vgg_loss.item()
        average /= len(pair_loader)
    return average


from scipy.stats import entropy
from torchvision.models.inception import inception_v3


def inception_score(imgs, cuda=True, batch_size=32, resize=False, splits=1):
    """Computes the inception score of the generated images imgs
    imgs -- Torch dataset of (3xHxW) numpy images normalized in the range [-1, 1]
    cuda -- whether or not to run on GPU
    batch_size -- batch size for feeding into Inception v3
    splits -- number of splits (When calculating IS, the whole data set is divided into several parts. Calculates IS for each part)
    return: mean and std of IS of each part
    """
    N = len(imgs)

    assert batch_size > 0
    assert N > batch_size

    # Set up dtype
    if cuda:
        dtype = torch.cuda.FloatTensor
    else:
        if torch.cuda.is_available():
            print("WARNING: You have a CUDA device, so you should probably set cuda=True")
        dtype = torch.FloatTensor

    # Set up dataloader
    dataloader = torch.utils.data.DataLoader(imgs, batch_size=batch_size)  # default: drop_last=false

    # Load inception model
    inception_model = inception_v3(pretrained=True, transform_input=False).type(dtype)
    inception_model.eval()
    up = nn.Upsample(size=(299, 299), mode='bilinear').type(dtype)  # Inception requires 299x299 input

    def get_pred(x):
        if resize:
            x = up(x)
        x = inception_model(x)
        return F.softmax(x).data.cpu().numpy()

    # Get predictions
    preds = np.zeros((N, 1000))

    for i, batch in enumerate(dataloader, 0):
        batch = batch.type(dtype)
        batch_size_i = batch.size()[0]
        preds[i * batch_size:i * batch_size + batch_size_i] = get_pred(batch)

    # Now compute the mean kl-div
    split_scores = []

    for k in range(splits):
        part = preds[k * (N // splits): (k + 1) * (N // splits), :]
        py = np.mean(part, axis=0)
        scores = []
        for i in range(part.shape[0]):
            pyx = part[i, :]
            scores.append(entropy(pyx, py))
        split_scores.append(np.exp(np.mean(scores)))

    return np.mean(split_scores), np.std(split_scores)


class ImageDataset(torch.utils.data.Dataset):
    def __init__(self, image_folder, transform):
        self.image_folder = image_folder
        self.transform = transform
        self.file_names = os.listdir(self.image_folder)

    def __getitem__(self, idx):
        image_path = os.path.join(self.image_folder, self.file_names[idx])
        image = Image.open(image_path)
        if self.transform:
            image = self.transform(image)
        return image

    def __len__(self):
        return len(self.file_names)


def inception_score_in_folder(image_folder):
    """
    return Inception Score given an image folder
    :param image_folder: str, path to a folder
    """
    transform = torchvision.transforms.Compose([
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
    ])
    imageset = ImageDataset(image_folder, transform)
    return inception_score(imageset, cuda=True, batch_size=20, resize=True, splits=10)
