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
"""
A collection of "vanilla" transforms for intensity adjustment
https://github.com/Project-MONAI/MONAI/wiki/MONAI_Design
"""

from collections.abc import Iterable
from typing import Any, Optional, Sequence, Tuple, Union
from warnings import warn

import numpy as np
import torch
from scipy.special import comb

from monai.networks.layers import GaussianFilter
from monai.transforms.compose import Randomizable, Transform
from monai.transforms.utils import rescale_array
from monai.utils import ensure_tuple_size


class RandGaussianNoise(Randomizable, Transform):
    """
    Add Gaussian noise to image.

    Args:
        prob: Probability to add Gaussian noise.
        mean: Mean or “centre” of the distribution.
        std: Standard deviation (spread) of distribution.
    """

    def __init__(self, prob: float = 0.1, mean: Union[Sequence[float], float] = 0.0, std: float = 0.1) -> None:
        self.prob = prob
        self.mean = mean
        self.std = std
        self._do_transform = False
        self._noise = None

    def randomize(self, im_shape: Sequence[int]) -> None:
        self._do_transform = self.R.random() < self.prob
        self._noise = self.R.normal(self.mean, self.R.uniform(0, self.std), size=im_shape)

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        self.randomize(img.shape)
        assert self._noise is not None
        return img + self._noise.astype(img.dtype) if self._do_transform else img


class ShiftIntensity(Transform):
    """
    Shift intensity uniformly for the entire image with specified `offset`.

    Args:
        offset: offset value to shift the intensity of image.
    """

    def __init__(self, offset: float) -> None:
        self.offset = offset

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        return (img + self.offset).astype(img.dtype)


class RandShiftIntensity(Randomizable, Transform):
    """
    Randomly shift intensity with randomly picked offset.
    """

    def __init__(self, offsets: Union[Tuple[float, float], float], prob: float = 0.1) -> None:
        """
        Args:
            offsets: offset range to randomly shift.
                if single number, offset value is picked from (-offsets, offsets).
            prob: probability of shift.
        """
        if isinstance(offsets, (int, float)):
            self.offsets = (min(-offsets, offsets), max(-offsets, offsets))
        else:
            assert len(offsets) == 2, "offsets should be a number or pair of numbers."
            self.offsets = (min(offsets), max(offsets))

        self.prob = prob
        self._do_transform = False

    def randomize(self, data: Optional[Any] = None) -> None:
        self._offset = self.R.uniform(low=self.offsets[0], high=self.offsets[1])
        self._do_transform = self.R.random() < self.prob

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        self.randomize()
        if not self._do_transform:
            return img
        shifter = ShiftIntensity(self._offset)
        return shifter(img)


class ScaleIntensity(Transform):
    """
    Scale the intensity of input image to the given value range (minv, maxv).
    If `minv` and `maxv` not provided, use `factor` to scale image by ``v = v * (1 + factor)``.
    """

    def __init__(
        self, minv: Optional[float] = 0.0, maxv: Optional[float] = 1.0, factor: Optional[float] = None
    ) -> None:
        """
        Args:
            minv: minimum value of output data.
            maxv: maximum value of output data.
            factor: factor scale by ``v = v * (1 + factor)``.
        """
        self.minv = minv
        self.maxv = maxv
        self.factor = factor

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.

        Raises:
            ValueError: When ``self.minv=None`` or ``self.maxv=None`` and ``self.factor=None``. Incompatible values.

        """
        if self.minv is not None and self.maxv is not None:
            return rescale_array(img, self.minv, self.maxv, img.dtype)
        elif self.factor is not None:
            return (img * (1 + self.factor)).astype(img.dtype)
        else:
            raise ValueError("Incompatible values: minv=None or maxv=None and factor=None.")


class RandScaleIntensity(Randomizable, Transform):
    """
    Randomly scale the intensity of input image by ``v = v * (1 + factor)`` where the `factor`
    is randomly picked from (factors[0], factors[0]).
    """

    def __init__(self, factors: Union[Tuple[float, float], float], prob: float = 0.1) -> None:
        """
        Args:
            factors: factor range to randomly scale by ``v = v * (1 + factor)``.
                if single number, factor value is picked from (-factors, factors).
            prob: probability of scale.

        """
        if isinstance(factors, (int, float)):
            self.factors = (min(-factors, factors), max(-factors, factors))
        else:
            assert len(factors) == 2, "factors should be a number or pair of numbers."
            self.factors = (min(factors), max(factors))

        self.prob = prob
        self._do_transform = False

    def randomize(self, data: Optional[Any] = None) -> None:
        self.factor = self.R.uniform(low=self.factors[0], high=self.factors[1])
        self._do_transform = self.R.random() < self.prob

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        self.randomize()
        if not self._do_transform:
            return img
        scaler = ScaleIntensity(minv=None, maxv=None, factor=self.factor)
        return scaler(img)


class NormalizeIntensity(Transform):
    """
    Normalize input based on provided args, using calculated mean and std if not provided
    (shape of subtrahend and divisor must match. if 0, entire volume uses same subtrahend and
    divisor, otherwise the shape can have dimension 1 for channels).
    This transform can normalize only non-zero values or entire image, and can also calculate
    mean and std on each channel separately.

    Args:
        subtrahend: the amount to subtract by (usually the mean).
        divisor: the amount to divide by (usually the standard deviation).
        nonzero: whether only normalize non-zero values.
        channel_wise: if using calculated mean and std, calculate on each channel separately
            or calculate on the entire image directly.
    """

    def __init__(
        self,
        subtrahend: Optional[np.ndarray] = None,
        divisor: Optional[np.ndarray] = None,
        nonzero: bool = False,
        channel_wise: bool = False,
    ) -> None:
        if subtrahend is not None or divisor is not None:
            assert isinstance(subtrahend, np.ndarray) and isinstance(
                divisor, np.ndarray
            ), "subtrahend and divisor must be set in pair and in numpy array."
        self.subtrahend = subtrahend
        self.divisor = divisor
        self.nonzero = nonzero
        self.channel_wise = channel_wise

    def _normalize(self, img: np.ndarray) -> np.ndarray:
        slices = (img != 0) if self.nonzero else np.ones(img.shape, dtype=np.bool_)
        if np.any(slices):
            if self.subtrahend is not None and self.divisor is not None:
                img[slices] = (img[slices] - self.subtrahend[slices]) / self.divisor[slices]
            else:
                img[slices] = (img[slices] - np.mean(img[slices])) / np.std(img[slices])
        return img

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`, assuming `img` is a channel-first array if `self.channel_wise` is True,
        """
        if self.channel_wise:
            for i, d in enumerate(img):
                img[i] = self._normalize(d)
        else:
            img = self._normalize(img)

        return img


class ThresholdIntensity(Transform):
    """
    Filter the intensity values of whole image to below threshold or above threshold.
    And fill the remaining parts of the image to the `cval` value.

    Args:
        threshold: the threshold to filter intensity values.
        above: filter values above the threshold or below the threshold, default is True.
        cval: value to fill the remaining parts of the image, default is 0.
    """

    def __init__(self, threshold: float, above: bool = True, cval: float = 0.0) -> None:
        assert isinstance(threshold, (int, float)), "threshold must be a float or int number."
        self.threshold = threshold
        self.above = above
        self.cval = cval

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        return np.where(img > self.threshold if self.above else img < self.threshold, img, self.cval).astype(img.dtype)


class ScaleIntensityRange(Transform):
    """
    Apply specific intensity scaling to the whole numpy array.
    Scaling from [a_min, a_max] to [b_min, b_max] with clip option.

    Args:
        a_min: intensity original range min.
        a_max: intensity original range max.
        b_min: intensity target range min.
        b_max: intensity target range max.
        clip: whether to perform clip after scaling.
    """

    def __init__(self, a_min: float, a_max: float, b_min: float, b_max: float, clip: bool = False) -> None:
        self.a_min = a_min
        self.a_max = a_max
        self.b_min = b_min
        self.b_max = b_max
        self.clip = clip

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        if self.a_max - self.a_min == 0.0:
            warn("Divide by zero (a_min == a_max)", Warning)
            return img - self.a_min + self.b_min

        img = (img - self.a_min) / (self.a_max - self.a_min)
        img = img * (self.b_max - self.b_min) + self.b_min
        if self.clip:
            img = np.clip(img, self.b_min, self.b_max)

        return img


class AdjustContrast(Transform):
    """
    Changes image intensity by gamma. Each pixel/voxel intensity is updated as::

        x = ((x - min) / intensity_range) ^ gamma * intensity_range + min

    Args:
        gamma: gamma value to adjust the contrast as function.
    """

    def __init__(self, gamma: float) -> None:
        assert isinstance(gamma, (int, float)), "gamma must be a float or int number."
        self.gamma = gamma

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        epsilon = 1e-7
        img_min = img.min()
        img_range = img.max() - img_min
        return np.power(((img - img_min) / float(img_range + epsilon)), self.gamma) * img_range + img_min


class RandAdjustContrast(Randomizable, Transform):
    """
    Randomly changes image intensity by gamma. Each pixel/voxel intensity is updated as::

        x = ((x - min) / intensity_range) ^ gamma * intensity_range + min

    Args:
        prob: Probability of adjustment.
        gamma: Range of gamma values.
            If single number, value is picked from (0.5, gamma), default is (0.5, 4.5).
    """

    def __init__(self, prob: float = 0.1, gamma: Union[Sequence[float], float] = (0.5, 4.5)) -> None:
        self.prob = prob

        if isinstance(gamma, (int, float)):
            assert gamma > 0.5, "if gamma is single number, must greater than 0.5 and value is picked from (0.5, gamma)"
            self.gamma = (0.5, gamma)
        else:
            assert len(gamma) == 2, "gamma should be a number or pair of numbers."
            self.gamma = (min(gamma), max(gamma))

        self._do_transform = False
        self.gamma_value = None

    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random_sample() < self.prob
        self.gamma_value = self.R.uniform(low=self.gamma[0], high=self.gamma[1])

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        self.randomize()
        assert self.gamma_value is not None
        if not self._do_transform:
            return img
        adjuster = AdjustContrast(self.gamma_value)
        return adjuster(img)


class ScaleIntensityRangePercentiles(Transform):
    """
    Apply range scaling to a numpy array based on the intensity distribution of the input.

    By default this transform will scale from [lower_intensity_percentile, upper_intensity_percentile] to [b_min, b_max], where
    {lower,upper}_intensity_percentile are the intensity values at the corresponding percentiles of ``img``.

    The ``relative`` parameter can also be set to scale from [lower_intensity_percentile, upper_intensity_percentile] to the
    lower and upper percentiles of the output range [b_min, b_max]

    For example:

    .. code-block:: python
        :emphasize-lines: 11, 22

        image = np.array(
            [[[1, 2, 3, 4, 5],
              [1, 2, 3, 4, 5],
              [1, 2, 3, 4, 5],
              [1, 2, 3, 4, 5],
              [1, 2, 3, 4, 5],
              [1, 2, 3, 4, 5]]])

        # Scale from lower and upper image intensity percentiles
        # to output range [b_min, b_max]
        scaler = ScaleIntensityRangePercentiles(10, 90, 0, 200, False, False)
        print(scaler(image))
        [[[0., 50., 100., 150., 200.],
          [0., 50., 100., 150., 200.],
          [0., 50., 100., 150., 200.],
          [0., 50., 100., 150., 200.],
          [0., 50., 100., 150., 200.],
          [0., 50., 100., 150., 200.]]]

        # Scale from lower and upper image intensity percentiles
        # to lower and upper percentiles of the output range [b_min, b_max]
        rel_scaler = ScaleIntensityRangePercentiles(10, 90, 0, 200, False, True)
        print(rel_scaler(image))
        [[[20., 60., 100., 140., 180.],
          [20., 60., 100., 140., 180.],
          [20., 60., 100., 140., 180.],
          [20., 60., 100., 140., 180.],
          [20., 60., 100., 140., 180.],
          [20., 60., 100., 140., 180.]]]


    Args:
        lower: lower intensity percentile.
        upper: upper intensity percentile.
        b_min: intensity target range min.
        b_max: intensity target range max.
        clip: whether to perform clip after scaling.
        relative: whether to scale to the corresponding percentiles of [b_min, b_max].
    """

    def __init__(
        self, lower: float, upper: float, b_min: float, b_max: float, clip: bool = False, relative: bool = False
    ) -> None:
        assert 0.0 <= lower <= 100.0, "Percentiles must be in the range [0, 100]"
        assert 0.0 <= upper <= 100.0, "Percentiles must be in the range [0, 100]"
        self.lower = lower
        self.upper = upper
        self.b_min = b_min
        self.b_max = b_max
        self.clip = clip
        self.relative = relative

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """
        Apply the transform to `img`.
        """
        a_min = np.percentile(img, self.lower)
        a_max = np.percentile(img, self.upper)
        b_min = self.b_min
        b_max = self.b_max

        if self.relative:
            b_min = ((self.b_max - self.b_min) * (self.lower / 100.0)) + self.b_min
            b_max = ((self.b_max - self.b_min) * (self.upper / 100.0)) + self.b_min

        scalar = ScaleIntensityRange(a_min=a_min, a_max=a_max, b_min=b_min, b_max=b_max, clip=False)
        img = scalar(img)

        if self.clip:
            img = np.clip(img, self.b_min, self.b_max)

        return img


class MaskIntensity(Transform):
    """
    Mask the intensity values of input image with the specified mask data.
    Mask data must have the same spatial size as the input image, and all
    the intensity values of input image corresponding to `0` in the mask
    data will be set to `0`, others will keep the original value.

    Args:
        mask_data: if mask data is single channel, apply to evey channel
            of input image. if multiple channels, the channel number must
            match input data. mask_data will be converted to `bool` values
            by `mask_data > 0` before applying transform to input image.

    """

    def __init__(self, mask_data: np.ndarray) -> None:
        self.mask_data = mask_data

    def __call__(self, img: np.ndarray, mask_data: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Args:
            mask_data: if mask data is single channel, apply to evey channel
                of input image. if multiple channels, the channel number must
                match input data. mask_data will be converted to `bool` values
                by `mask_data > 0` before applying transform to input image.

        Raises:
            ValueError: When ``mask_data`` and ``img`` channels differ and ``mask_data`` is not single channel.

        """
        mask_data_ = self.mask_data > 0 if mask_data is None else mask_data > 0
        if mask_data_.shape[0] != 1 and mask_data_.shape[0] != img.shape[0]:
            raise ValueError(
                "When mask_data is not single channel, mask_data channels must match img, "
                f"got img={img.shape[0]} mask_data={mask_data_.shape[0]}."
            )

        return img * mask_data_


class GaussianSmooth(Transform):
    """
    Apply Gaussian smooth to the input data based on specified `sigma` parameter.
    A default value `sigma=1.0` is provided for reference.

    Args:
        sigma: if a list of values, must match the count of spatial dimensions of input data,
            and apply every value in the list to 1 spatial dimension. if only 1 value provided,
            use it for all spatial dimensions.

    """

    def __init__(self, sigma: Union[Sequence[float], float] = 1.0) -> None:
        self.sigma = sigma

    def __call__(self, img: np.ndarray) -> np.ndarray:
        gaussian_filter = GaussianFilter(img.ndim - 1, self.sigma)
        input_data = torch.as_tensor(np.ascontiguousarray(img), dtype=torch.float).unsqueeze(0)
        return gaussian_filter(input_data).squeeze(0).detach().numpy()


class RandGaussianSmooth(Randomizable, Transform):
    """
    Apply Gaussian smooth to the input data based on randomly selected `sigma` parameters.

    Args:
        sigma_x: randomly select sigma value for the first spatial dimension.
        sigma_y: randomly select sigma value for the second spatial dimension if have.
        sigma_z: randomly select sigma value for the third spatial dimension if have.
        prob: probability of Gaussian smooth.

    """

    def __init__(
        self,
        sigma_x: Tuple[float, float] = (0.25, 1.5),
        sigma_y: Tuple[float, float] = (0.25, 1.5),
        sigma_z: Tuple[float, float] = (0.25, 1.5),
        prob: float = 0.1,
    ) -> None:
        self.sigma_x = sigma_x
        self.sigma_y = sigma_y
        self.sigma_z = sigma_z
        self.prob = prob
        self._do_transform = False

    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random_sample() < self.prob
        self.x = self.R.uniform(low=self.sigma_x[0], high=self.sigma_x[1])
        self.y = self.R.uniform(low=self.sigma_y[0], high=self.sigma_y[1])
        self.z = self.R.uniform(low=self.sigma_z[0], high=self.sigma_z[1])

    def __call__(self, img: np.ndarray) -> np.ndarray:
        self.randomize()
        if not self._do_transform:
            return img
        sigma = ensure_tuple_size(tup=(self.x, self.y, self.z), dim=img.ndim - 1)
        return GaussianSmooth(sigma=sigma)(img)


class GaussianSharpen(Transform):
    """
    Sharpen images using the Gaussian Blur filter.
    Referring to: http://scipy-lectures.org/advanced/image_processing/auto_examples/plot_sharpen.html.
    The algorithm is shown as below

    .. code-block:: python

        blurred_f = gaussian_filter(img, sigma1)
        filter_blurred_f = gaussian_filter(blurred_f, sigma2)
        img = blurred_f + alpha * (blurred_f - filter_blurred_f)

    A set of default values `sigma1=3.0`, `sigma2=1.0` and `alpha=30.0` is provide for reference.

    Args:
        sigma1: sigma parameter for the first gaussian kernel. if a list of values, must match the count
            of spatial dimensions of input data, and apply every value in the list to 1 spatial dimension.
            if only 1 value provided, use it for all spatial dimensions.
        sigma2: sigma parameter for the second gaussian kernel. if a list of values, must match the count
            of spatial dimensions of input data, and apply every value in the list to 1 spatial dimension.
            if only 1 value provided, use it for all spatial dimensions.
        alpha: weight parameter to compute the final result.

    """

    def __init__(
        self,
        sigma1: Union[Sequence[float], float] = 3.0,
        sigma2: Union[Sequence[float], float] = 1.0,
        alpha: float = 30.0,
    ) -> None:
        self.sigma1 = sigma1
        self.sigma2 = sigma2
        self.alpha = alpha

    def __call__(self, img: np.ndarray) -> np.ndarray:
        gaussian_filter1 = GaussianFilter(img.ndim - 1, self.sigma1)
        gaussian_filter2 = GaussianFilter(img.ndim - 1, self.sigma2)
        input_data = torch.as_tensor(np.ascontiguousarray(img), dtype=torch.float).unsqueeze(0)
        blurred_f = gaussian_filter1(input_data)
        filter_blurred_f = gaussian_filter2(blurred_f)
        return (blurred_f + self.alpha * (blurred_f - filter_blurred_f)).squeeze(0).detach().numpy()


class RandGaussianSharpen(Randomizable, Transform):
    """
    Sharpen images using the Gaussian Blur filter based on randomly selected `sigma1`, `sigma2` and `alpha`.
    The algorithm is :py:class:`monai.transforms.GaussianSharpen`.

    Args:
        sigma1_x: randomly select sigma value for the first spatial dimension of first gaussian kernel.
        sigma1_y: randomly select sigma value for the second spatial dimension(if have) of first gaussian kernel.
        sigma1_z: randomly select sigma value for the third spatial dimension(if have) of first gaussian kernel.
        sigma2_x: randomly select sigma value for the first spatial dimension of second gaussian kernel.
            if only 1 value `X` provided, it must be smaller than `sigma1_x` and randomly select from [X, sigma1_x].
        sigma2_y: randomly select sigma value for the second spatial dimension(if have) of second gaussian kernel.
            if only 1 value `Y` provided, it must be smaller than `sigma1_y` and randomly select from [Y, sigma1_y].
        sigma2_z: randomly select sigma value for the third spatial dimension(if have) of second gaussian kernel.
            if only 1 value `Z` provided, it must be smaller than `sigma1_z` and randomly select from [Z, sigma1_z].
        alpha: randomly select weight parameter to compute the final result.
        prob: probability of Gaussian sharpen.

    """

    def __init__(
        self,
        sigma1_x: Tuple[float, float] = (0.5, 1.0),
        sigma1_y: Tuple[float, float] = (0.5, 1.0),
        sigma1_z: Tuple[float, float] = (0.5, 1.0),
        sigma2_x: Union[Tuple[float, float], float] = 0.5,
        sigma2_y: Union[Tuple[float, float], float] = 0.5,
        sigma2_z: Union[Tuple[float, float], float] = 0.5,
        alpha: Tuple[float, float] = (10.0, 30.0),
        prob: float = 0.1,
    ) -> None:
        self.sigma1_x = sigma1_x
        self.sigma1_y = sigma1_y
        self.sigma1_z = sigma1_z
        self.sigma2_x = sigma2_x
        self.sigma2_y = sigma2_y
        self.sigma2_z = sigma2_z
        self.alpha = alpha
        self.prob = prob
        self._do_transform = False

    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random_sample() < self.prob
        self.x1 = self.R.uniform(low=self.sigma1_x[0], high=self.sigma1_x[1])
        self.y1 = self.R.uniform(low=self.sigma1_y[0], high=self.sigma1_y[1])
        self.z1 = self.R.uniform(low=self.sigma1_z[0], high=self.sigma1_z[1])
        sigma2_x = (self.sigma2_x, self.x1) if not isinstance(self.sigma2_x, Iterable) else self.sigma2_x
        sigma2_y = (self.sigma2_y, self.y1) if not isinstance(self.sigma2_y, Iterable) else self.sigma2_y
        sigma2_z = (self.sigma2_z, self.z1) if not isinstance(self.sigma2_z, Iterable) else self.sigma2_z
        self.x2 = self.R.uniform(low=sigma2_x[0], high=sigma2_x[1])
        self.y2 = self.R.uniform(low=sigma2_y[0], high=sigma2_y[1])
        self.z2 = self.R.uniform(low=sigma2_z[0], high=sigma2_z[1])
        self.a = self.R.uniform(low=self.alpha[0], high=self.alpha[1])

    def __call__(self, img: np.ndarray) -> np.ndarray:
        self.randomize()
        if not self._do_transform:
            return img
        sigma1 = ensure_tuple_size(tup=(self.x1, self.y1, self.z1), dim=img.ndim - 1)
        sigma2 = ensure_tuple_size(tup=(self.x2, self.y2, self.z2), dim=img.ndim - 1)
        return GaussianSharpen(sigma1=sigma1, sigma2=sigma2, alpha=self.a)(img)


class RandHistogramShift(Randomizable, Transform):
    """
    Apply random nonlinear transform to the image's intensity histogram.

    Args:
        num_control_points: number of control points governing the nonlinear intensity mapping.
            a smaller number of control points allows for larger intensity shifts. if two values provided, number of
            control points selecting from range (min_value, max_value).
        prob: probability of histogram shift.
    """

    def __init__(self, num_control_points: Union[Tuple[int, int], int] = 10, prob: float = 0.1) -> None:

        if isinstance(num_control_points, int):
            assert num_control_points > 2, "num_control_points should be greater than or equal to 3"
            self.num_control_points = (num_control_points, num_control_points)
        else:
            assert len(num_control_points) == 2, "num_control points should be a number or a pair of numbers"
            assert min(num_control_points) > 2, "num_control_points should be greater than or equal to 3"
            self.num_control_points = (min(num_control_points), max(num_control_points))
        self.prob = prob
        self._do_transform = False

    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random() < self.prob
        num_control_point = self.R.randint(self.num_control_points[0], self.num_control_points[1] + 1)
        self.reference_control_points = np.linspace(0, 1, num_control_point)
        self.floating_control_points = np.copy(self.reference_control_points)
        for i in range(1, num_control_point - 1):
            self.floating_control_points[i] = self.R.uniform(
                self.floating_control_points[i - 1], self.floating_control_points[i + 1]
            )

    def __call__(self, img: np.ndarray) -> np.ndarray:
        self.randomize()
        if not self._do_transform:
            return img
        img_min, img_max = img.min(), img.max()
        reference_control_points_scaled = self.reference_control_points * (img_max - img_min) + img_min
        floating_control_points_scaled = self.floating_control_points * (img_max - img_min) + img_min
        return np.interp(img, reference_control_points_scaled, floating_control_points_scaled).astype(img.dtype)


class RandLocalPixelShuffle(Randomizable, Transform):
    def __init__(self, prob: float = 0.5, num_block_range: Union[Sequence[int], int] = [50,200]):
        self.num_block_range = (num_block_range,num_block_range+1) if isinstance(num_block_range, int) else num_block_range
        self.prob = min(max(prob, 0.0), 1.0)

    def randomize(self, data: Optional[Any] = None) -> None:
        shape = data.squeeze().shape
        self.num_block = self.R.randint(self.num_block_range[0], self.num_block_range[1], 1)[0]
        if len(shape) == 3:
            self.img_rows, self.img_cols, self.img_deps = shape
            self.dim = 3
        elif len(shape) == 2:
            self.img_rows, self.img_cols = shape
            self.dim = 2
        else:
            raise ValueError('Only support 2D and 3D images')
        self._do_transform = self.R.random() < self.prob

    def generate_pos(self):
        self.block_noise_size_x = self.R.randint(1, self.img_rows//10)
        self.block_noise_size_y = self.R.randint(1, self.img_cols//10)
        self.block_noise_size_z = self.R.randint(1, self.img_deps//10) if self.dim ==3 else None
        self.noise_x = self.R.randint(0, self.img_rows-self.block_noise_size_x)
        self.noise_y = self.R.randint(0, self.img_cols-self.block_noise_size_y)
        self.noise_z = self.R.randint(0, self.img_deps-self.block_noise_size_z) if self.dim == 3 else None

    def __call__(self, image):
        self.randomize(image)
        if not self._do_transform:
            return image
        
        image_temp = image.copy()
        for _ in range(self.num_block):
            self.generate_pos()
            if self.dim == 3:
                window = image[0, 
                             self.noise_x:self.noise_x+self.block_noise_size_x, 
                             self.noise_y:self.noise_y+self.block_noise_size_y, 
                             self.noise_z:self.noise_z+self.block_noise_size_z,
                            ]
            elif self.dim == 2:
                window = image[0, 
                             self.noise_x:self.noise_x+self.block_noise_size_x, 
                             self.noise_y:self.noise_y+self.block_noise_size_y,
                            ]
            window = window.flatten()
            np.random.shuffle(window)
            if self.dim == 3:
                window = window.reshape((self.block_noise_size_x, self.block_noise_size_y, self.block_noise_size_z))
                image_temp[0, 
                           self.noise_x:self.noise_x+self.block_noise_size_x, 
                           self.noise_y:self.noise_y+self.block_noise_size_y, 
                           self.noise_z:self.noise_z+self.block_noise_size_z ] = window
            elif self.dim == 2:
                window = window.reshape((self.block_noise_size_x, self.block_noise_size_y))
                image_temp[0, 
                           self.noise_x:self.noise_x+self.block_noise_size_x, 
                           self.noise_y:self.noise_y+self.block_noise_size_y ] = window

        return image_temp


class RandImageInpainting(Randomizable, Transform):
    def __init__(self, prob: float = 0.5, num_block_range: Union[Sequence[int], int] = [3,6]):
        self.num_block_range = (num_block_range,num_block_range+1) if isinstance(num_block_range, int) else num_block_range
        self.prob = min(max(prob, 0.0), 1.0)

    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random() < self.prob
        self.num_block = self.R.randint(self.num_block_range[0], self.num_block_range[1], 1)[0]
        shape = data.squeeze().shape
        if len(shape) == 3:
            self.img_rows, self.img_cols, self.img_deps = shape
            self.dim = 3
        elif len(shape) == 2:
            self.img_rows, self.img_cols = shape
            self.dim = 2
        else:
            raise ValueError('Only support 2D and 3D images')

    def generate_pos(self):
        self.block_noise_size_x = self.R.randint(self.img_rows//6, self.img_rows//3)
        self.block_noise_size_y = self.R.randint(self.img_cols//6, self.img_cols//3)
        self.block_noise_size_z = self.R.randint(self.img_deps//6, self.img_deps//3) if self.dim ==3 else None
        self.noise_x = self.R.randint(3, self.img_rows-self.block_noise_size_x-3)
        self.noise_y = self.R.randint(3, self.img_cols-self.block_noise_size_y-3)
        self.noise_z = self.R.randint(3, self.img_deps-self.block_noise_size_z-3) if self.dim ==3 else None

    def __call__(self, image):
        self.randomize(image)
        if not self._do_transform:
            return image

        for _ in range(self.num_block):
            self.generate_pos()
            if self.dim == 3:
                image[:, 
                      self.noise_x:self.noise_x+self.block_noise_size_x, 
                      self.noise_y:self.noise_y+self.block_noise_size_y, 
                      self.noise_z:self.noise_z+self.block_noise_size_z] = np.random.rand(self.block_noise_size_x, 
                                                                                          self.block_noise_size_y, 
                                                                                          self.block_noise_size_z ) * 1.0
            elif self.dim == 2:
                image[:, 
                      self.noise_x:self.noise_x+self.block_noise_size_x, 
                      self.noise_y:self.noise_y+self.block_noise_size_y] = np.random.rand(self.block_noise_size_x, 
                                                                                          self.block_noise_size_y ) * 1.0                
        return image


class RandImageOutpainting(Randomizable, Transform):
    def __init__(self, prob: float = 0.5, num_block_range: Union[Sequence[int], int] = [3,6]):
        self.num_block_range = (num_block_range,num_block_range+1) if isinstance(num_block_range, int) else num_block_range
        self.prob = min(max(prob, 0.0), 1.0)

    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random() < self.prob
        self.num_block = self.R.randint(self.num_block_range[0], self.num_block_range[1], 1)[0]
        shape = data.squeeze().shape
        if len(shape) == 3:
            self.img_rows, self.img_cols, self.img_deps = shape
            self.dim = 3
        elif len(shape) == 2:
            self.img_rows, self.img_cols = shape
            self.dim = 2
        else:
            raise ValueError('Only support 2D and 3D images')

    def generate_pos(self):
        ratio = 8
        self.block_noise_size_x = self.img_rows - self.R.randint(3*self.img_rows//ratio, 4*self.img_rows//ratio)
        self.block_noise_size_y = self.img_cols - self.R.randint(3*self.img_cols//ratio, 4*self.img_cols//ratio)
        self.block_noise_size_z = self.img_deps - self.R.randint(3*self.img_deps//ratio, 4*self.img_deps//ratio) if self.dim ==3 else None
        self.noise_x = self.R.randint(3, self.img_rows-self.block_noise_size_x-3)
        self.noise_y = self.R.randint(3, self.img_cols-self.block_noise_size_y-3)
        self.noise_z = self.R.randint(3, self.img_deps-self.block_noise_size_z-3) if self.dim ==3 else None

    def __call__(self, image):
        self.randomize(image)
        if not self._do_transform:
            return image

        self.generate_pos()
        image_temp = image.copy()
        x = self.R.rand(*image.shape) * 1.0
        if self.dim == 3:
            x[:, 
              self.noise_x:self.noise_x+self.block_noise_size_x, 
              self.noise_y:self.noise_y+self.block_noise_size_y, 
              self.noise_z:self.noise_z+self.block_noise_size_z] = image_temp[:, 
                                                                              self.noise_x:self.noise_x+self.block_noise_size_x, 
                                                                              self.noise_y:self.noise_y+self.block_noise_size_y, 
                                                                              self.noise_z:self.noise_z+self.block_noise_size_z]
        elif self.dim == 2:
            x[:, 
              self.noise_x:self.noise_x+self.block_noise_size_x, 
              self.noise_y:self.noise_y+self.block_noise_size_y] = image_temp[:, 
                                                                              self.noise_x:self.noise_x+self.block_noise_size_x, 
                                                                              self.noise_y:self.noise_y+self.block_noise_size_y]
        for _ in range(self.num_block):
            self.generate_pos()
            if self.dim == 3:
                x[:, 
                  self.noise_x:self.noise_x+self.block_noise_size_x, 
                  self.noise_y:self.noise_y+self.block_noise_size_y, 
                  self.noise_z:self.noise_z+self.block_noise_size_z] = image_temp[:, 
                                                                                  self.noise_x:self.noise_x+self.block_noise_size_x, 
                                                                                  self.noise_y:self.noise_y+self.block_noise_size_y, 
                                                                                  self.noise_z:self.noise_z+self.block_noise_size_z]
            elif self.dim == 2:
                x[:, 
                  self.noise_x:self.noise_x+self.block_noise_size_x, 
                  self.noise_y:self.noise_y+self.block_noise_size_y] = image_temp[:, 
                                                                                  self.noise_x:self.noise_x+self.block_noise_size_x, 
                                                                                  self.noise_y:self.noise_y+self.block_noise_size_y]
        return x


class RandNonlinear(Randomizable, Transform):
    def __init__(self, prob: float = 0.5):
        self.prob = prob
    
    def randomize(self, data: Optional[Any] = None) -> None:
        self._do_transform = self.R.random() < self.prob
        self.points = [[0, 0], [self.R.random(), self.R.random()], [self.R.random(), self.R.random()], [1, 1]]
    
    def bernstein_poly(self, i, n, t):
        """
        The Bernstein polynomial of n, i as a function of t
        """

        return comb(n, i) * ( t**(n-i) ) * (1 - t)**i

    def bezier_curve(self, points, nTimes=10000):
        """
        Given a set of control points, return the
        bezier curve defined by the control points.
        Control points should be a list of lists, or list of tuples
        such as [ [1,1], 
                    [2,3], 
                    [4,5], ..[Xn, Yn] ]
            nTimes is the number of time steps, defaults to 1000
            See http://processingjs.nihongoresources.com/bezierinfo/
        """

        nPoints = len(points)
        xPoints = np.array([p[0] for p in points])
        yPoints = np.array([p[1] for p in points])

        t = np.linspace(0.0, 1.0, nTimes)

        polynomial_array = np.array([ self.bernstein_poly(i, nPoints-1, t) for i in range(0, nPoints)   ])
        
        xvals = np.dot(xPoints, polynomial_array)
        yvals = np.dot(yPoints, polynomial_array)

        return xvals, yvals

    def __call__(self, image):
        self.randomize(image)
        if not self._do_transform:
            return image

        xpoints = [p[0] for p in self.points]
        ypoints = [p[1] for p in self.points]
        xvals, yvals = self.bezier_curve(self.points, nTimes=10000)
        if self.R.random() < 0.5:
            # Half change to get flip
            xvals = np.sort(xvals)
        else:
            xvals, yvals = np.sort(xvals), np.sort(yvals)
        
        nonlinear_x = np.interp(image, xvals, yvals)
        return nonlinear_x