# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Dict, List, Optional, Sequence, Tuple, Union

import torch


class CommonKeys:
    """
    A set of common keys for dictionary based supervised training process.
    `IMAGE` is the input image data.
    `LABEL` is the training or evaluation label of segmentation or classification task.
    `PRED` is the prediction data of model output.
    `LOSS` is the loss value of current iteration.
    `INFO` is some useful information during training or evaluation, like loss value, etc.

    """

    IMAGE = "image"
    LABEL = "label"
    PRED = "pred"
    LOSS = "loss"


class GanKeys:
    """
    A set of common keys for generative adversarial networks.
    """

    REALS = "reals"
    FAKES = "fakes"
    LATENTS = "latents"
    GLOSS = "g_loss"
    DLOSS = "d_loss"

class RcnnKeys:
    """
    A set of common keys for RCNN-style networks.
    """    

    IMAGE = "image"
    LABEL = "label"
    ROI_LABEL = "roi_label"
    ROI_BBOX = "roi_bbox"
    ROI_MASK = 'roi_mask'


def get_devices_spec(devices: Optional[Sequence[torch.device]] = None) -> List[torch.device]:
    """
    Get a valid specification for one or more devices. If `devices` is None get devices for all CUDA devices available.
    If `devices` is and zero-length structure a single CPU compute device is returned. In any other cases `devices` is
    returned unchanged.

    Args:
        devices: list of devices to request, None for all GPU devices, [] for CPU.

    Raises:
        RuntimeError: When all GPUs are selected (``devices=None``) but no GPUs are available.

    Returns:
        list of torch.device: list of devices.

    """
    if devices is None:
        devices = [torch.device(f"cuda:{d:d}") for d in range(torch.cuda.device_count())]

        if len(devices) == 0:
            raise RuntimeError("No GPU devices available.")

    elif len(devices) == 0:
        devices = [torch.device("cpu")]

    else:
        devices = list(devices)

    return devices


def default_prepare_batch(
    batchdata: Dict[str, torch.Tensor]
) -> Union[Tuple[torch.Tensor, Optional[torch.Tensor]], torch.Tensor]:
    assert isinstance(batchdata, dict), "default prepare_batch expects dictionary input data."
    if RcnnKeys.ROI_BBOX in batchdata or RcnnKeys.ROI_LABEL in batchdata:
        return batchdata[RcnnKeys.IMAGE], batchdata[RcnnKeys.LABEL], \
               batchdata[RcnnKeys.ROI_BBOX], batchdata[RcnnKeys.ROI_LABEL]
    elif CommonKeys.LABEL in batchdata:
        return batchdata[CommonKeys.IMAGE], batchdata[CommonKeys.LABEL]
    elif GanKeys.REALS in batchdata:
        return batchdata[GanKeys.REALS]
    else:
        return batchdata[CommonKeys.IMAGE], None


def default_make_latent(num_latents: int, latent_size: int, real_data: Optional[torch.Tensor] = None) -> torch.Tensor:
    return torch.randn(num_latents, latent_size)
