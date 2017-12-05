from os import listdir
import os.path as osp
from random import shuffle
import random
import shlex
import subprocess
import sqlite3

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
import psutil
import pandas as pd
from pandas.io.sql import DatabaseError
from psycopg2.extensions import register_adapter, AsIs

import psycopg2
from psycopg2.sql import SQL, Identifier
import torch
import torch.nn.functional as F


def register_numpy_types():
    # Credit: https://github.com/musically-ut/psycopg2_numpy_ext
    """Register the AsIs adapter for following types from numpy:
      - numpy.int8
      - numpy.int16
      - numpy.int32
      - numpy.int64
      - numpy.float16
      - numpy.float32
      - numpy.float64
      - numpy.float128
    """
    for typ in ['int8', 'int16', 'int32', 'int64',
                'float16', 'float32', 'float64', 'float128',
                'bool_']:
        register_adapter(np.__getattribute__(typ), AsIs)


def get_max_of_db_column(db_connect_str, table_name, column_name):
    conn = psycopg2.connect(db_connect_str)
    cur = conn.cursor()
    parameters = [Identifier(column_name), Identifier(table_name)]
    cur.execute(SQL("SELECT MAX({}) FROM {}").format(*parameters))
    max_value = cur.fetchone()[0]
    cur.close()
    conn.close()
    return max_value


def insert_into_table(db_connect_str, table_name, key_value_pairs):
    register_numpy_types()
    table_name = Identifier(table_name)
    fields = [Identifier(field) for field in key_value_pairs.keys()]
    values = [v.__name__ if callable(v) or isinstance(v, type) else v
              for v in key_value_pairs.values()]
    conn = psycopg2.connect(db_connect_str)
    cur = conn.cursor()
    insert_part = "INSERT INTO {}"
    field_positions = get_format_positions(len(key_value_pairs), "{}")
    fields_part = "({})".format(field_positions)
    value_positions = get_format_positions(len(key_value_pairs), "%s")
    values_part = "VALUES ({})".format(value_positions)
    query = insert_part + " " + fields_part + " " + values_part
    query = SQL(query).format(table_name, *fields)
    cur.execute(query, values)
    conn.commit()
    cur.close()
    conn.close()


def update_table(db_connect_str, table_name, key_value_pairs):
    row_id = key_value_pairs.copy().pop("id")
    register_numpy_types()
    table_name = Identifier(table_name)
    fields = [Identifier(field) for field in key_value_pairs.keys()]
    values = [v.__name__ if callable(v) or isinstance(v, type) else v
              for v in key_value_pairs.values()]
    conn = psycopg2.connect(db_connect_str)
    cur = conn.cursor()
    update_part = "UPDATE {}"
    placeholders = get_format_positions(len(key_value_pairs), "{} = %s")
    set_part = "SET {}".format(placeholders)
    where_part = "WHERE id = %s"
    query = update_part + " " + set_part + " " + where_part
    query = SQL(query).format(table_name, *fields)
    parameters = values + [row_id]
    cur.execute(query, parameters)
    conn.commit()
    cur.close()
    conn.close()


def get_format_positions(num, form):
    positions = ''
    for _ in range(num-1):
        positions += (form + ", ")
    positions += form
    return positions


def choose(x):
    return np.random.choice(x)


def print_separator():
    print("-"*80)


def get_database_path(here):
    return osp.join(osp.join(here, "logs"), "database.sqlite")


def load_sqlite_table(database_path, table_name):
    """Returns (table, connection). table is a pandas DataFrame."""
    conn = sqlite3.connect(database_path)
    try:
        df = pd.read_sql("SELECT * FROM %s" % table_name, conn)
        #  print("\nLoading %s table from SQLite3 database." % table_name)
    except DatabaseError as e:
        if 'no such table' in e.args[0]:
            print("\nNo such table: %s" % table_name)
            print("Create the table before loading it. " +
                  "Consider using the create_sqlite_table function")
            raise DatabaseError
        else:
            print(e)
            raise Exception("Failed to create %s table. Unknown error." %
                            table_name)
    return df, conn


def create_sqlite_table(database_path, table_name, table_header):
    """Returns (table, connection). table is a pandas DataFrame."""
    conn = sqlite3.connect(database_path)
    print("\nCreating %s table in SQLite3 database." % table_name)
    df = pd.DataFrame(columns=table_header)
    df.to_sql(table_name, conn, index=False)
    return df, conn


def create_log(filepath, headers):
    if not osp.exists(filepath):
        with open(filepath, 'w') as f:
            f.write(','.join(headers) + '\n')


def get_RAM():
    return psutil.virtual_memory().used


def git_hash():
    cmd = 'git log -n 1 --pretty="%h"'
    hash = subprocess.check_output(shlex.split(cmd)).strip()
    return hash


def transform_portrait(img):
    img = np.array(img, dtype=np.uint8)
    img = img[:, :, ::-1]  # RGB -> BGR
    img = img.astype(np.float64)
    mean_bgr = np.array([104.00698793, 116.66876762, 122.67891434])
    img -= mean_bgr
    img = img.transpose(2, 0, 1)  # HxWxC --> CxHxW
    return img


def split_trn_val(num_train, valid_size=0.2, shuffle=False):
    indices = list(range(num_train))
    if shuffle:
        np.random.shuffle(indices)
    split = int(np.floor(valid_size * num_train))
    trn_indices, val_indices = indices[split:], indices[:split]
    return trn_indices, val_indices


def cross_entropy2d(score, target, weight=None, size_average=True):
    log_p = F.log_softmax(score)

    # Flatten the score tensor
    n, c, h, w = score.size()
    log_p = log_p.transpose(1, 2).transpose(2, 3).contiguous().view(-1, c)
    # Remove guesses corresponding to "unknown" labels
    # (labels that are less than zero)
    log_p = log_p[target.view(n * h * w, 1).repeat(1, c) >= 0]
    log_p = log_p.view(-1, c)

    # Remove "unknown" labels (labels that are less than zero)
    # Also, flatten the target tensor
    # TODO: Replace this entire function with nn.functional.cross_entropy
    #   with ignore_index set to -1.
    mask = target >= 0
    target = target[mask]

    loss = F.nll_loss(log_p, target, weight=weight, size_average=False)

    if size_average:
        loss /= mask.data.sum()
    return loss


def scoretensor2mask(scoretensor):
    """
    - scoretensor (3D torch tensor) (CxHxW): Each channel contains the scores
        for the corresponding category in the image.
    Returns a numpy array.
    """
    _, labels = scoretensor.max(0)  # Get labels w/ highest scores
    labels_np = labels.numpy().astype(np.uint8)
    mask = labels_np * 255
    return mask


def detransform_portrait(img, mean="voc"):
    """
    - img (torch tensor)
    Returns a numpy array.
    """
    if mean == "voc":
        mean_bgr = np.array([104.00698793, 116.66876762, 122.67891434])
    else:
        raise ValueError("unknown mean")
    #  img = img.numpy().astype(np.float64)
    img = img.transpose((1, 2, 0))  # CxHxW --> HxWxC
    #  img *= 255
    img += mean_bgr
    img = img[:, :, ::-1]  # BGR -> RGB
    img = img.astype(np.uint8)
    return img


def detransform_mask(mask):
    #  mask = mask.numpy()
    mask = mask.astype(np.uint8)
    mask *= 255
    return mask


def mask_image(img, mask, opacity=1.00, bg=False):
    """
        - img (PIL)
        - mask (PIL)
        - opacity (float) (default: 1.00)
    Returns a PIL image.
    """
    blank = Image.new('RGB', img.size, color=0)
    if bg:
        masked_image = Image.composite(blank, img, mask)
    else:
        masked_image = Image.composite(img, blank, mask)
    if opacity < 1:
        masked_image = Image.blend(img, masked_image, opacity)
    return masked_image


def show_portrait_pred_mask(portrait, preds, mask, start_iteration,
                            evaluation_interval,
                            opacity=None, bg=False, fig=None):
    """
    Args:
        - portrait (torch tensor)
        - preds (list of np.ndarray): list of mask predictions
        - mask (torch tensor)
    A visualization function.
    Returns nothing.
    """
    # Gather images
    images = []
    titles = []
    cmaps = []

    #  ### Prepare portrait
    portrait_pil = Image.fromarray(portrait)
    images.append(portrait)
    titles.append("input")
    cmaps.append(None)

    #  ### Prepare predictions
    for i, pred in enumerate(preds):
        pred_pil = Image.fromarray(pred)
        if opacity:
            pred_pil = mask_image(portrait_pil, pred_pil, opacity, bg)
        images.append(pred_pil)
        titles.append("iter. %d" % (start_iteration + i * evaluation_interval))
        cmaps.append("gray")

    #  ### Prepare target mask
    if opacity:
        mask_pil = Image.fromarray(mask)
        mask = mask_image(portrait_pil, mask_pil, opacity, bg)
    images.append(mask)
    titles.append("target")
    cmaps.append("gray")

    # Show images
    cols = 5
    rows = int(np.ceil(len(images) / cols))
    w = 12
    h = rows * (w / cols + 1)
    figsize = (w, h)  # width x height
    plots(images, titles=titles, cmap=cmaps, rows=rows, cols=cols,
          figsize=figsize, fig=fig)


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)


def get_fnames(d, random=False):
    fnames = [d + f for f in listdir(d) if osp.isfile(osp.join(d, f))]
    print("Number of files found in %s: %s" % (d, len(fnames)))
    if random:
        shuffle(fnames)
    return fnames


def rm_dir_and_ext(filepath):
    return filepath.split('/')[-1].split('.')[-2]


def get_flickr_id(portrait_fname):
    """
    Input (string): '../data/portraits/flickr/cropped/portraits/00074.jpg'
    Output (int): 74
    """
    return int(rm_dir_and_ext(portrait_fname))


def get_lines(fname):
    '''Read lines, strip, and split.'''
    with open(fname) as f:
        content = f.readlines()
    content = [x.strip().split() for x in content]
    return content


def hist(data, figsize=(6, 3)):
    plt.figure(figsize=figsize)
    plt.hist(data)
    plt.show()


def plot_portraits_and_masks(portraits, masks):
    assert len(portraits) == len(masks)
    fig, axes = plt.subplots(2, 4, figsize=(12, 6))
    fig.tight_layout()
    for i, ax in enumerate(axes.flat):
        if i < 4:
            ax.imshow(portraits[i], interpolation="spline16")
        else:
            mask = gray2rgb(masks[i-4])
            ax.imshow(mask)
        ax.set_xticks([])
        ax.set_yticks([])
    plt.show()


def gray2rgb(gray):
    w, h = gray.shape
    rgb = np.empty((w, h, 3), dtype=np.uint8)
    rgb[:, :, 2] = rgb[:, :, 1] = rgb[:, :, 0] = gray
    return rgb


def plots(imgs, figsize=(12, 12), rows=None, cols=None,
          interp=None, titles=None, cmap='gray',
          fig=None):
    if not isinstance(imgs, list):
        imgs = [imgs]
    imgs = [np.array(img) for img in imgs]
    if not isinstance(cmap, list):
        if imgs[0].ndim == 2:
            cmap = 'gray'
        cmap = [cmap] * len(imgs)
    if not isinstance(interp, list):
        interp = [interp] * len(imgs)
    n = len(imgs)
    if not rows and not cols:
        cols = n
        rows = 1
    elif not rows:
        rows = cols
    elif not cols:
        cols = rows
    if not fig:
        rows = int(np.ceil(len(imgs) / cols))
        w = 12
        h = rows * (w / cols + 1)
        figsize = (w, h)
        fig = plt.figure(figsize=figsize)
    fontsize = 13 if cols == 5 else 16
    fig.set_figheight(figsize[1], forward=True)
    fig.clear()
    for i in range(len(imgs)):
        sp = fig.add_subplot(rows, cols, i+1)
        if titles:
            sp.set_title(titles[i], fontsize=fontsize)
        plt.imshow(imgs[i], interpolation=interp[i], cmap=cmap[i])
        plt.axis('off')
        plt.subplots_adjust(0, 0, 1, 1, .1, 0)
        #  plt.tight_layout()
    if fig:
        fig.canvas.draw()
