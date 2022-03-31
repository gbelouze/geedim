"""
   Copyright 2021 Dugal Harris - dugalh@gmail.com

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

##
# Functions to download and export Earth Engine images

import multiprocessing
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from itertools import product
from typing import Tuple, Dict

import ee
import numpy as np
import rasterio as rio
import requests
from rasterio import Affine
from rasterio import MemoryFile
from rasterio.crs import CRS
from rasterio.windows import Window
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from tqdm import tqdm


def requests_retry_session(
        retries=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504),
        session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


class DownloadTile:
    def __init__(self, image: ee.Image, transform: Affine, window: Window):
        self._image = image
        self._window = window
        self._transform = transform * Affine.translation(window.col_off, window.row_off)
        self._shape = (window.height, window.width)

    @property
    def transform(self) -> Affine:
        return self._transform

    @property
    def shape(self) -> Affine:
        return self._shape

    @property
    def window(self) -> Window:
        return self._window

    def download(self, session):
        # TODO: progress bars for each tile - something like mamba does?
        session = session if session else requests

        # get image download url
        url = self._image.getDownloadURL(
            dict(crs=self._image.projection().crs(), crs_transform=tuple(self._transform)[:6],
                 dimensions=self._shape[::-1], filePerBand=False, fileFormat='GeoTIFF'))

        # download into buffer
        buffer = BytesIO()
        resp = session.get(url, stream=True)
        for chunk in resp.iter_content(chunk_size=1024):
            if chunk:
                buffer.write(chunk)
                buffer.flush()

        # extract geotiff from zipped buffer
        zip_file = zipfile.ZipFile(buffer)
        ext_buffer = BytesIO(zip_file.read(zip_file.filelist[0]))

        # read the geotiff with rasterio memory file
        with MemoryFile(ext_buffer) as mem_file:
            with mem_file.open() as ds:
                array = ds.read()

        return array


class DownloadImage:
    def __init__(self, image: ee.Image, **kwargs):
        # TODO: masking & nodata in profile below
        kwargs.update(fileFormat='GeoTIFF', filePerBand=False)
        self._image, _ = image.prepare_for_export(kwargs)
        self._info = self._image.getInfo()
        self._band_info = self._info['bands'][0]
        self._transform = Affine(*self._band_info['crs_transform'])
        if 'origin' in self._band_info:
            self._transform *= Affine.translation(*self._band_info['origin'])
        self._shape = self._band_info['dimensions'][::-1]
        self._count = len(self._info['bands'])
        self._dtype = self._get_dtype()
        self._threads = multiprocessing.cpu_count() - 1
        rio_crs = CRS.from_string(kwargs['crs'] if 'crs' in kwargs else self._band_info['crs'])
        self._profile = dict(driver='GTiff', dtype=self._dtype, nodata=0, width=self._shape[1], height=self._shape[0],
                             count=self.count, crs=rio_crs, transform=self._transform, compress='deflate',
                             interleave='band', tiled=True)
        self._out_lock = threading.Lock()

    @property
    def image(self) -> ee.Image:
        return self._image

    @property
    def info(self) -> Dict:
        return self._im_info

    @property
    def band_info(self) -> Dict:
        return self._band_info

    @property
    def transform(self) -> Affine:
        return self._transform

    @property
    def shape(self) -> Tuple[int, int]:
        return self._shape

    @property
    def count(self) -> int:
        return self._count

    @property
    def profile(self) -> Dict:
        return self._profile

    def _get_dtype(self):
        # TODO: each band has different min/max vals
        ee_data_type = self._band_info['data_type']

        if ee_data_type['precision'] == 'int':
            # TODO: dtype_bits will not be correct for cases where part of the range is <0, but the range is not symm about 0
            dtype_range = ee_data_type['max'] - ee_data_type['min']
            dtype_bits = 2 ** np.ceil(np.log2(np.log2(dtype_range))).astype('int')
            poss_int_bits = [8, 16, 32, 64]
            if not dtype_bits in poss_int_bits:
                return 'int64'  # revert to int64 in unusual cases
            else:
                return f'int{dtype_bits}' if ee_data_type['min'] < 0 else f'uint{dtype_bits}'
        elif ee_data_type['precision'] == 'double':
            return 'float64'
        else:
            raise ValueError(f'Unknown image data type: {ee_data_type}')

    def _get_tile_shape(self, max_download_size=33554432, max_grid_dimension=10000) -> Tuple[int, int]:
        """Internal function to find a tile shape that satisfies download limits and is roughly square"""
        # TODO incorporate max_grid_dimension
        # find the ttl number of tiles needed to satisfy max_download_size
        dtype_size = np.dtype(self._dtype).itemsize
        image_size = np.prod(np.int64((self.count, dtype_size, *self._shape)))
        num_tiles = np.ceil(image_size / max_download_size)

        # increment num_tiles if it is prime
        # (this is so that we can factorize num_tiles into x,y dimension components, and don't have all tiles along
        # one dimension)
        def is_prime(x):
            for d in range(2, int(x ** 0.5) + 1):
                if x % d == 0:
                    return False
            return True

        if is_prime(num_tiles):
            num_tiles += 1

        # factorise num_tiles into num of tiles down x,y axes
        def factors(x):
            facts = np.arange(1, x + 1)
            facts = facts[np.mod(x, facts) == 0]
            return np.vstack((facts, x / facts)).transpose()

        fact_num_tiles = factors(num_tiles)

        # choose the factors that produce a roughly square (as close as possible) tile shape
        fact_aspect_ratios = fact_num_tiles[:, 0] / fact_num_tiles[:, 1]
        aspect_ratio = self._shape[0] / self._shape[1]
        fact_idx = np.argmin(np.abs(fact_aspect_ratios - aspect_ratio))
        shape_num_tiles = fact_num_tiles[fact_idx, :]

        # find the tile shape and clip to max_grid_dimension if necessary
        tile_shape = np.ceil(self._shape / shape_num_tiles).astype('int')
        tile_shape[tile_shape > max_grid_dimension] = max_grid_dimension

        return tuple(tile_shape.tolist())

    def tiles(self):
        """
        Iterator over the image tiles.

        Yields:
        -------
        tile: DownloadTile
            An image tile that can be downloaded.
        """
        tile_shape = self._get_tile_shape()

        # split the image up into tiles of at most tile_shape
        start_range = product(range(0, self._shape[0], tile_shape[0]), range(0, self._shape[1], tile_shape[1]))
        for tile_start in start_range:
            tile_stop = np.add(tile_start, tile_shape)
            tile_stop = np.clip(tile_stop, a_min=None, a_max=self._shape)
            clip_tile_shape = (tile_stop - tile_start).tolist()  # tolist is just to convert to native int
            tile_window = Window(tile_start[1], tile_start[0], clip_tile_shape[1], clip_tile_shape[0])
            yield DownloadTile(self._image, self._transform, tile_window)

    def download(self, filename, resampling='near', overwrite=False):
        # TODO: resampling.  in __init__ where we know if it is composite or not?

        session = requests_retry_session(5, status_forcelist=[500, 502, 503, 504])
        with rio.Env(GDAL_NUM_THREADS='ALL_CPUs'), rio.open(filename, 'w', **self._profile) as out_ds:
            def download_tile(tile):
                tile_array = tile.download(session)
                with self._out_lock:
                    out_ds.write(tile_array, window=tile.window)

            with ThreadPoolExecutor(max_workers=self._threads) as executor:
                futures = [executor.submit(download_tile, tile) for tile in self.tiles()]
                for future in tqdm(as_completed(futures), total=len(futures)):
                    future.result()
