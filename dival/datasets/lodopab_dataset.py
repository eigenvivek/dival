# -*- coding: utf-8 -*-
"""Provides `LoDoPaBDataset`."""
import os
from warnings import warn
from math import ceil
import numpy as np
import h5py
from zipfile import ZipFile
from tqdm import tqdm
from odl.discr.lp_discr import uniform_discr
import odl.tomo
from dival.datasets.dataset import Dataset
from dival.config import CONFIG, set_config
from dival.util.constants import MU_MAX
from dival.util.zenodo_download import download_zenodo_record, input_yes_no


DATA_PATH = CONFIG['lodopab_dataset']['data_path']
NUM_SAMPLES_PER_FILE = 128
PHOTONS_PER_PIXEL = 4096
ORIG_MIN_PHOTON_COUNT = 0.1
MIN_PT = [-0.13, -0.13]
MAX_PT = [0.13, 0.13]


def download_lodopab():
    global DATA_PATH
    print('Before downloading, please make sure to have enough free disk '
          'space (~150GB). After unpacking, 114.7GB will be used.')
    print("path to store LoDoPaB-CT dataset (default '{}'):".format(DATA_PATH))
    inp = input()
    if inp:
        DATA_PATH = inp
        set_config('lodopab_dataset/data_path', DATA_PATH)
    os.makedirs(DATA_PATH, exist_ok=True)
    ZENODO_RECORD_ID = '3384092'
    success = download_zenodo_record(ZENODO_RECORD_ID, DATA_PATH)
    print('download of LoDoPaB-CT dataset {}'.format('successful' if success
                                                     else 'failed'))
    if not success:
        return False
    file_list = ['observation_train.zip',      'ground_truth_train.zip',
                 'observation_validation.zip', 'ground_truth_validation.zip',
                 'observation_test.zip',       'ground_truth_test.zip']
    print('unzipping zip files, this can take several minutes', flush=True)
    for file in tqdm(file_list, desc='unzip'):
        filename = os.path.join(DATA_PATH, file)
        with ZipFile(filename, 'r') as f:
            f.extractall(DATA_PATH)
        os.remove(filename)
    return True


class LoDoPaBDataset(Dataset):
    """
    A simulated low dose CT dataset based on real reconstructions from the
    `LIDC-IDRI
    <https://wiki.cancerimagingarchive.net/display/Public/LIDC-IDRI>`_ dataset.

    The dataset contains 42895 pairs of images and projection data.
    For simulation, a ray transform with parallel beam geometry using 1000
    angles and 513 detector pixels is used. Poisson noise corresponding to 4096
    incident photons per pixel before attenuation is applied to the projection
    data. The images have a size of 362x362 px.

    A ray transform that corresponds to the noiseless forward operator is
    available via the `get_ray_trafo` method of this dataset.

    Attributes
    ----------
    space
        ``(space[0], space[1])``, where
            ``space[0]``
                ``odl.uniform_discr([0., -0.1838], [3.1416, 0.1838],
                (1000, 513), dtype='float32')``
            ``space[1]``
                ``odl.uniform_discr(min_pt, max_pt, (362, 362),
                dtype='float32'))``, with `min_pt` and `max_pt` parameters
                passed to :meth:`__init__`
    shape
        ``(362, 362)``
    train_len
        ``35820``
    validation_len
       ``3522``
    test_len
        ``3553``
    random_access
        ``True``
    num_elements_per_sample
        ``2``
    """
    def __init__(self, min_pt=None, max_pt=None, observation_model='post-log',
                 min_photon_count=None):
        """
        Parameters
        ----------
        min_pt : [float, float], optional
            Minimum values of the lp space. Default: ``[-0.13, -0.13]``.
        max_pt : [float, float], optional
            Maximum values of the lp space. Default: ``[0.13, 0.13]``.
        observation_model : {'post-log', 'pre-log'}, optional
            The observation model to use.
            The default is ``'post-log'``.

            ``'post-log'``
                Observations are linearly related to the normalized ground
                truth via the ray transform, ``obs = ray_trafo(gt) + noise``.
                Note that the scaling of the observations matches the
                normalized ground truth, i.e., they are divided by the linear
                attenuation of 3071 HU.
            ``'pre-log'``
                Observations are non-linearly related to the ground truth, as
                given by the Beer-Lambert law.
                The model is
                ``obs = exp(-ray_trafo(gt * MU(3071 HU))) + noise``,
                where `MU(3071 HU)` is the factor, by which the ground truth
                was normalized.
        min_photon_count : float, optional
            Replacement value for a simulated photon count of zero.
            If ``observation_model == 'post-log'``, a value greater than zero
            is required in order to avoid undefined values. The default is 0.1,
            both for ``'post-log'`` and ``'pre-log'`` model.
        """
        global DATA_PATH
        NUM_ANGLES = 1000
        NUM_DET_PIXELS = 513
        self.shape = ((NUM_ANGLES, NUM_DET_PIXELS), (362, 362))
        self.num_elements_per_sample = 2
        if min_pt is None:
            min_pt = MIN_PT
        if max_pt is None:
            max_pt = MAX_PT
        domain = uniform_discr(min_pt, max_pt, self.shape[1], dtype=np.float32)
        if observation_model == 'post-log':
            self.post_log = True
        elif observation_model == 'pre-log':
            self.post_log = False
        else:
            raise ValueError("`observation_model` must be 'post-log' or "
                             "'pre-log', not '{}'".format(observation_model))
        if min_photon_count is None or min_photon_count <= 1.:
            self.min_photon_count = min_photon_count
        else:
            self.min_photon_count = 1.
            warn('`min_photon_count` changed from {} to 1.'.format(
                min_photon_count))
        self.train_len = 35820
        self.validation_len = 3522
        self.test_len = 3553
        self.random_access = True

        while not self.check_for_lodopab():
            print('The LoDoPaB-CT dataset could not be found under the '
                  "configured path '{}'.".format(
                      CONFIG['lodopab_dataset']['data_path']))
            print('Do you want to download it now? (y: download, n: input '
                  'other path)')
            download = input_yes_no()
            if download:
                success = download_lodopab()
                if not success:
                    raise RuntimeError('lodopab dataset not available, '
                                       'download failed')
            else:
                print('Path to LoDoPaB dataset:')
                DATA_PATH = input()
                set_config('lodopab_dataset/data_path', DATA_PATH)

        self.geometry = odl.tomo.parallel_beam_geometry(
            domain, num_angles=NUM_ANGLES, det_shape=(NUM_DET_PIXELS,))
        range_ = uniform_discr(self.geometry.partition.min_pt,
                               self.geometry.partition.max_pt,
                               self.shape[0], dtype=np.float32)
        super().__init__(space=(range_, domain))

    def check_for_lodopab(self):
        """Fast check whether first and last file of each dataset part exist
        under the configured data path.

        Returns
        -------
        exists : bool
            Whether LoDoPaB seems to exist.
        """
        for part in ['train', 'validation', 'test']:
            first_file = os.path.join(
                DATA_PATH, 'observation_{}_000.hdf5'.format(part))
            last_file = os.path.join(
                DATA_PATH, 'observation_{}_{:03d}.hdf5'.format(
                    part, ceil(self.get_len(part) / NUM_SAMPLES_PER_FILE) - 1))
            if not (os.path.exists(first_file) and os.path.exists(last_file)):
                return False
        return True

    def __get_observation_trafo(self, num_samples=1):
        if (self.min_photon_count is None or
                self.min_photon_count == ORIG_MIN_PHOTON_COUNT):
            if self.post_log:
                def observation_trafo(out):
                    pass
            else:
                def observation_trafo(obs):
                    obs *= MU_MAX
                    np.exp(-obs, out=obs)
        else:
            shape = (self.shape[0] if num_samples == 1 else
                     (num_samples,) + self.shape[0])
            mask = np.empty(shape, dtype=np.bool)
            thres0 = 0.5 * (
                -np.log(ORIG_MIN_PHOTON_COUNT/PHOTONS_PER_PIXEL)
                - np.log(1/PHOTONS_PER_PIXEL)) / MU_MAX
            if self.post_log:
                def observation_trafo(obs):
                    np.greater_equal(obs, thres0, out=mask)
                    obs[mask] = -np.log(self.min_photon_count
                                        / PHOTONS_PER_PIXEL) / MU_MAX
            else:
                def observation_trafo(obs):
                    np.greater_equal(obs, thres0, out=mask)
                    obs *= MU_MAX
                    np.exp(-obs, out=obs)
                    obs[mask] = self.min_photon_count/PHOTONS_PER_PIXEL
        return observation_trafo

    def generator(self, part='train'):
        """Yield pairs of low dose observations and (virtual) ground truth.

        Parameters
        ----------
        part : {``'train'``, ``'validation'``, ``'test'``}, optional
            The data part. Default is ``'train'``.

        Yields
        ------
        (observation, ground_truth)
            `observation` : odl element with shape ``(1000, 513)``
                The values depend on the
                `observation_model` and `min_photon_count` parameters that were
                passed to :meth:`__init__`.
            `ground_truth` : odl element with shape ``(362, 362)``
                The values lie in the range ``[0., 1.]``.
        """
        num_files = ceil(self.get_len(part) / NUM_SAMPLES_PER_FILE)
        observation_trafo = self.__get_observation_trafo()
        for i in range(num_files):
            with h5py.File(
                    os.path.join(DATA_PATH, 'ground_truth_{}_{:03d}.hdf5'
                                            .format(part, i)), 'r') as file:
                ground_truth_data = file['data'][:]
            with h5py.File(
                    os.path.join(DATA_PATH, 'observation_{}_{:03d}.hdf5'
                                            .format(part, i)), 'r') as file:
                observation_data = file['data'][:]
            for gt_arr, obs_arr in zip(ground_truth_data, observation_data):
                ground_truth = self.space[1].element(gt_arr)
                observation = self.space[0].element(obs_arr)
                observation_trafo(observation)

                yield (observation, ground_truth)

    def get_ray_trafo(self, impl=None):
        """
        Return the ray transform that is a noiseless version of the forward
        operator.

        Parameters
        ----------
        impl : {``'skimage'``, ``'astra_cpu'``, ``'astra_cuda'``}, optional
            The backend implementation passed to
            :class:`odl.tomo.RayTransform`.

        Returns
        -------
        ray_trafo : odl operator
            The ray transform that corresponds to the noiseless map from
            362 x 362 images to the ``-log`` of their projections (sinograms).
        """
        return odl.tomo.RayTransform(self.space[1], self.geometry,
                                     range=self.space[0], impl=impl)

    ray_trafo = property(get_ray_trafo)
    """
    Read-only property calling :meth:`get_ray_trafo` with default parameters.
    """

    def get_sample(self, index, part='train', out=None):
        """
        Get single sample of the dataset.
        Returns a pair of (virtual) ground truth and its low dose observation,
        of which either part can be left out by option.

        Parameters
        ----------
        index : int
            The index into the dataset part.
        part : {``'train'``, ``'validation'``, ``'test'``}, optional
            The data part. Default is ``'train'``.
        out : tuple of array-likes or bools, optional
            ``out==(out_observation, out_ground_truth)``

            out_observation : array-like or bool
                Shape ``(1000, 513)``.
                If an odl element or array is passed, the observation is
                written to it.
                If ``True``, a new odl element holding the observation is
                created (the default).
                If ``False``, no observation is returned.
            out_ground_truth : array-like or bool
                Shape ``(362, 362)``.
                If an odl element or array is passed, the ground truth is
                written to it.
                If ``True``, a new odl element holding the ground truth is
                created (the default).
                If ``False``, no ground truth is returned.

        Returns
        -------
        ``(observation, ground_truth)`` or `None`

            observation : odl element or :class:`np.ndarray` or `None`
                Depending on the value of ``out_observation`` (see parameter
                `out`), a newly created odl element, ``out_observation`` or
                `None` is returned.
                The observation values depend on the `observation_model` and
                `min_photon_count` parameters that were given to the
                constructor.

            ground_truth : odl element or :class:`np.ndarray` or `None`
                Depending on the value of ``out_ground_truth`` (see parameter
                `out`), a newly created odl element, ``out_ground_truth`` or
                `None` is returned.
                The values lie in the range ``[0., 1.]``.
        """
        len_part = self.get_len(part)
        if index >= len_part or index < -len_part:
            raise IndexError("index {} out of bounds for part '{}' ({:d})"
                             .format(index, part, len_part))
        if index < 0:
            index += len_part
        (out_observation, out_ground_truth) = out
        file_index = index // NUM_SAMPLES_PER_FILE
        index_in_file = index % NUM_SAMPLES_PER_FILE
        if isinstance(out_observation, bool):
            obs = self.space[0].zero() if out_observation else None
        else:
            obs = out_observation
        if isinstance(out_ground_truth, bool):
            gt = self.space[1].zero() if out_ground_truth else None
        else:
            gt = out_ground_truth
        if obs is not None:
            with h5py.File(
                    os.path.join(DATA_PATH,
                                 'observation_{}_{:03d}.hdf5'
                                 .format(part, file_index)), 'r') as file:
                file['data'].read_direct(np.asarray(obs)[np.newaxis],
                                         np.s_[index_in_file:index_in_file+1],
                                         np.s_[0:1])
            observation_trafo = self.__get_observation_trafo()
            observation_trafo(obs)
        if gt is not None:
            with h5py.File(
                    os.path.join(DATA_PATH,
                                 'ground_truth_{}_{:03d}.hdf5'
                                 .format(part, file_index)), 'r') as file:
                file['data'].read_direct(np.asarray(gt)[np.newaxis],
                                         np.s_[index_in_file:index_in_file+1],
                                         np.s_[0:1])
        return (obs, gt)

    def get_samples(self, key, part='train', out=None):
        """
        Get slice of the dataset.
        Returns a pair of (virtual) ground truth data and its low dose
        observation data, of which either part can be left out by option.

        Parameters
        ----------
        key : slice or range
            The indices into the dataset part.
        part : {``'train'``, ``'validation'``, ``'test'``}, optional
            The data part. Default is ``'train'``.
        out : tuple of arrays or bools, optional
            ``out==(out_observation, out_ground_truth)``

            out_observation : :class:`np.ndarray` or bool
                If an array is passed, the observation data is written to it.
                If ``True``, a new array holding the observation data is
                created (the default).
                If ``False``, no observation data is returned.
            out_ground_truth : :class:`np.ndarray` or bool
                If an array is passed, the ground truth data is written to it.
                If ``True``, a new array holding the ground truth data is
                created (the default).
                If ``False``, no ground truth data is returned.

        Returns
        -------
        ``(observation, ground_truth)`` or `None`

            observation : :class:`np.ndarray` or `None`
                Shape ``(samples, 1000, 513)``.
                Depending on the value of ``out_observation`` (see parameter
                `out`), a newly created array, ``out_observation`` or `None`
                is returned.
                The observation values depend on the `observation_model` and
                `min_photon_count` parameters that were given to the
                constructor.

            ground_truth : :class:`np.ndarray` or `None`
                Shape ``(samples, 362, 362)``.
                Depending on the value of ``out_ground_truth`` (see parameter
                `out`), a newly created array, ``out_ground_truth`` or `None`
                is returned.
                The values lie in the range ``[0., 1.]``.
        """
        len_part = self.get_len(part)
        if isinstance(key, slice):
            key_start = (0 if key.start is None else
                         (key.start if key.start >= 0 else
                          max(0, len_part+key.start)))
            key_stop = (len_part if key.stop is None else
                        (key.stop if key.stop >= 0 else
                         max(0, len_part+key.stop)))
            range_ = range(key_start, key_stop, key.step or 1)
        elif isinstance(key, range):
            range_ = key
        else:
            raise TypeError('`key` expected to have type `slice` or `range`')
        if range_.step < 0:
            raise ValueError('key {} invalid, negative steps are not '
                             'implemented yet'.format(key))
        if range_[-1] >= len_part:
            raise IndexError("key {} out of bounds for part '{}' ({:d})"
                             .format(key, part, len_part))
        range_files = range(range_[0] // NUM_SAMPLES_PER_FILE,
                            range_[-1] // NUM_SAMPLES_PER_FILE + 1)
        (out_observation, out_ground_truth) = out
        # compute slice objects
        slices_files = []
        slices_data = []
        data_count = 0
        for i in range_files:
            if i == range_files.start:
                start = range_.start % NUM_SAMPLES_PER_FILE
            else:
                start = (range_.start - i*NUM_SAMPLES_PER_FILE) % range_.step
            if i == range_files[-1]:
                stop = range_[-1] % NUM_SAMPLES_PER_FILE + 1
            else:
                __next_start = ((range_.start - (i+1)*NUM_SAMPLES_PER_FILE)
                                % range_.step)
                stop = (__next_start - range_.step) % NUM_SAMPLES_PER_FILE + 1
            s = slice(start, stop, range_.step)
            slices_files.append(s)
            len_slice = ceil((s.stop-s.start) / s.step)
            slices_data.append(slice(data_count, data_count+len_slice))
            data_count += len_slice
        # read data
        if isinstance(out_observation, bool):
            obs_arr = (np.empty((len(range_),) + self.shape[0],
                                dtype=np.float32) if out_observation else None)
        else:
            obs_arr = out_observation
        if isinstance(out_ground_truth, bool):
            gt_arr = (np.empty((len(range_),) + self.shape[1],
                               dtype=np.float32) if out_ground_truth else None)
        else:
            gt_arr = out_ground_truth
        if obs_arr is not None:
            for i, slc_f, slc_d in zip(range_files, slices_files, slices_data):
                with h5py.File(
                        os.path.join(DATA_PATH,
                                     'observation_{}_{:03d}.hdf5'
                                     .format(part, i)), 'r') as file:
                    file['data'].read_direct(obs_arr, slc_f, slc_d)
            observation_trafo = self.__get_observation_trafo(
                num_samples=len(obs_arr))
            observation_trafo(obs_arr)
        if gt_arr is not None:
            for i, slc_f, slc_d in zip(range_files, slices_files, slices_data):
                with h5py.File(
                        os.path.join(DATA_PATH,
                                     'ground_truth_{}_{:03d}.hdf5'
                                     .format(part, i)), 'r') as file:
                    file['data'].read_direct(gt_arr, slc_f, slc_d)
        return (obs_arr, gt_arr)

    def create_torch_dataset(self, part='train'):
        import torch

        class LoDoPaBDatasetTorch(Dataset):
            def __init__(self, part='train'):
                self.part = part
                self.dataset = LoDoPaBDataset()

            def __len__(self):
                return self.dataset.get_len(self.part)

            def __getitem__(self, idx):
                obs = np.empty((1,) + self.dataset.shape[0], dtype=np.float32)
                gt = np.empty((1,) + self.dataset.shape[1], dtype=np.float32)
                self.dataset.get_sample(idx, self.part, out=(obs, gt))
                return torch.from_numpy(obs), torch.from_numpy(gt)

        torch_dataset = LoDoPaBDatasetTorch(part=part)
        return torch_dataset
