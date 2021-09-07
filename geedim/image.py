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

# Functionality for wrapping, cloud/shadow masking and scoring Earth Engine images
import collections
import importlib.util
import logging

import ee
import numpy as np
import pandas as pd

from geedim import info


##
def split_id(image_id):
    """
    Split Earth Engine image ID into collection and index components

    Parameters
    ----------
    image_id: str
              Earth engine image ID

    Returns
    -------
        A tuple of strings: (collection name, image index)
    """
    index = image_id.split("/")[-1]
    ee_coll_name = "/".join(image_id.split("/")[:-1])
    return ee_coll_name, index


def get_info(ee_image, min=True):
    """
    Retrieve Earth Engine image metadata

    Parameters
    ----------
    ee_image : ee.Image
               The image whose information to retrieve
    min : bool, optional
          Retrieve the crs & scale corresponding to the band with the minimum (True) or maximum (False) scale
          (default: True)

    Returns
    -------
    gd_info : dict
              dictionary of image information with id, properties, bands, crs and scale keys
    """
    gd_info = dict(id=None, properties={}, bands=[], crs=None, scale=None)
    ee_info = ee_image.getInfo()    # retrieve image info from cloud

    if "id" in ee_info:
        gd_info["id"] = ee_info["id"]

    if "properties" in ee_info:
        gd_info["properties"] = ee_info["properties"]

    if "bands" in ee_info:
        # get scale & crs corresponding to min/max scale band (exclude 'EPSG:4326' (composite/constant) bands)
        band_df = pd.DataFrame(ee_info["bands"])
        scales = pd.DataFrame(band_df["crs_transform"].tolist())[0].abs().astype(float)
        band_df["scale"] = scales
        filt_band_df = band_df[(band_df.crs != "EPSG:4326") & (band_df.scale != 1)]
        if filt_band_df.shape[0] > 0:
            idx = filt_band_df.scale.idxmin() if min else filt_band_df.scale.idxmax()
            gd_info["crs"], gd_info["scale"] = filt_band_df.loc[idx, ["crs", "scale"]]

        # populate band metadata
        ee_coll_name = split_id(str(gd_info["id"]))[0]
        if ee_coll_name in info.ee_to_gd:  # include SR band metadata if it exists
            # use DataFrame to concat SR band metadata from collection_info with band IDs from the image
            gd_info["bands"] = info.collection_info[info.ee_to_gd[ee_coll_name]]["bands"].copy()
            sr_band_df = pd.DataFrame.from_dict(gd_info["bands"])
            band_df.index = band_df.id
            band_df = band_df.drop(index=sr_band_df.id)
            gd_info["bands"] += band_df[["id"]].to_dict("records")
        else:  # just use the image band IDs
            gd_info["bands"] = band_df[["id"]].to_dict("records")

    return gd_info


def get_projection(image, min=True):
    """
    Get the min/max scale projection of image bands.  Server side - no calls to getInfo().
    Adapted from from https://github.com/gee-community/gee_tools, MIT license

    Parameters
    ----------
    image : ee.Image, geedim.image.Image
            The image whose min/max projection to retrieve
    min: bool, optional
         Retrieve the projection corresponding to the band with the minimum (True) or maximum (False) scale
         [default: True]

    Returns
    -------
    ee.Projection
      The projection with the smallest scale
    """
    if isinstance(image, Image):
        image = image.ee_image

    bands = image.bandNames()

    transform = np.array([1, 0, 0, 0, 1, 0])
    if min:
        compare = ee.Number.lte
        init_proj = ee.Projection('EPSG:4326', list((1e100)*transform))
    else:
        compare = ee.Number.gte
        init_proj = ee.Projection('EPSG:4326', list((1e-100)*transform))

    def compare_scale(name, prev_proj):
        """ Server side comparison of band scales"""
        prev_proj = ee.Projection(prev_proj)
        prev_scale = prev_proj.nominalScale()

        curr_proj = image.select([name]).projection()
        curr_scale = ee.Number(curr_proj.nominalScale())

        # compare scales, excluding WGS84 bands (constant or composite bands)
        condition = (
            compare(curr_scale, prev_scale).And(curr_proj.crs().compareTo(ee.String("EPSG:4326"))).neq(ee.Number(0))
        )
        comp_proj = ee.Algorithms.If(condition, curr_proj, prev_proj)
        return ee.Projection(comp_proj)

    return ee.Projection(bands.iterate(compare_scale, init_proj))

if importlib.util.find_spec("rasterio"):    # if rasterio is installed
    import rasterio as rio
    from rasterio.warp import transform_geom

    def get_bounds(filename, expand=5):
        """
        Get a geojson polygon representing the bounds of an image

        Parameters
        ----------
        filename :  str, pathlib.Path
                    Path of the image file whose bounds to find
        expand :    int
                    percentage (0-100) by which to expand the bounds (default: 5)

        Returns
        -------
        bounds : dict
                 Geojson polygon
        crs : str
              image CRS as EPSG string
        """
        try:
            # GEE sets tif colorinterp tags incorrectly, suppress rasterio warning relating to this:
            # 'Sum of Photometric type-related color channels and ExtraSamples doesn't match SamplesPerPixel'
            logging.getLogger("rasterio").setLevel(logging.ERROR)
            with rio.open(filename) as im:
                bbox = im.bounds
                if (im.crs.linear_units == "metre") and (expand > 0):  # expand the bounding box
                    expand_x = (bbox.right - bbox.left) * expand / 100.0
                    expand_y = (bbox.top - bbox.bottom) * expand / 100.0
                    bbox_expand = rio.coords.BoundingBox(
                        bbox.left - expand_x,
                        bbox.bottom - expand_y,
                        bbox.right + expand_x,
                        bbox.top + expand_y,
                    )
                else:
                    bbox_expand = bbox

                coordinates = [
                    [bbox_expand.right, bbox_expand.bottom],
                    [bbox_expand.right, bbox_expand.top],
                    [bbox_expand.left, bbox_expand.top],
                    [bbox_expand.left, bbox_expand.bottom],
                    [bbox_expand.right, bbox_expand.bottom],
                ]

                bbox_expand_dict = dict(type="Polygon", coordinates=[coordinates])
                src_bbox_wgs84 = transform_geom(im.crs, "WGS84", bbox_expand_dict)  # convert to WGS84 geojson
        finally:
            logging.getLogger("rasterio").setLevel(logging.WARNING)

        ImageBounds = collections.namedtuple('ImageBounds', ['bounds', 'crs'])
        return ImageBounds(src_bbox_wgs84, im.crs.to_epsg())


## Image classes
class Image(object):
    def __init__(self, ee_image):
        """
        Basic class to wrap any ee.Image and provide access to metadara

        Parameters
        ----------
        ee_image : ee.Image
                   Image to wrap
        """
        self._ee_image = ee_image
        self._info = None

    @property
    def ee_image(self):
        """
        The wrapped image

        Returns
        -------
        : ee.Image
        """
        return self._ee_image

    @property
    def info(self):
        """
        Image information as from get_info()

        Returns
        -------
        : dict
        """
        if self._info is None:
            self._info = get_info(self._ee_image)
        return self._info

    @property
    def id(self):
        """
        Earth Engine image ID

        Returns
        -------
        : str
        """
        return self.info["id"]

    @property
    def crs(self):
        """
        Image CRS corresponding to minimum scale band, as EPSG string

        Returns
        -------
        : str
        """
        return self.info["crs"]

    @property
    def scale(self):
        """
        Scale (m) corresponding to minimum scale band

        Returns
        -------
        : float
        """
        return self.info["scale"]


class MaskedImage(Image):
    def __init__(self, ee_image, mask=False, scale_refl=False):
        """
        Base class to cloud/shadow mask and quality score Earth engine images from supported collections

        Parameters
        ----------
        ee_image : ee.Image
                   Earth engine image to wrap
        mask : bool, optional
               Apply a validity (cloud & shadow) mask to the image (default: False)
        scale_refl : bool, optional
                     Scale reflectance bands 0-10000 if they are not in that range already (default: False)
        """
        # prevent instantiation of base class(es)
        if not self.gd_coll_name in info.collection_info:
            raise NotImplementedError("This base class cannot be instantiated, use a derived class")

        # construct the cloud/shadow masks and cloudless score
        self._masks = self._get_image_masks(ee_image)
        self._score = self._get_image_score(ee_image)
        self._ee_image = self._process_image(
            ee_image, mask=mask, scale_refl=scale_refl, masks=self._masks, score=self._score
        )
        self._info = None
        self._projection = None

    @classmethod
    def from_id(cls, image_id, mask=False, scale_refl=False):
        """
        Earth engine image wrapper for cloud/shadow masking and quality scoring

        Parameters
        ----------
        image_id : str
                   ID of earth engine image to wrap
        mask : bool, optional
               Apply a validity (cloud & shadow) mask to the image (default: False)
        scale_refl : bool, optional
                     Scale reflectance bands 0-10000 if they are not in that range already (default: False)
        """
        # check image is from a supported collection
        ee_coll_name = split_id(image_id)[0]
        if ee_coll_name not in info.ee_to_gd:
            raise ValueError(f"Unsupported collection: {ee_coll_name}")

        # check this class supports the image's collection
        gd_coll_name = info.ee_to_gd[ee_coll_name]
        if gd_coll_name != cls._gd_coll_name:
            raise ValueError(f"{cls.__name__} only supports images from {info.gd_to_ee[cls._gd_coll_name]}")

        ee_image = ee.Image(image_id)
        return cls(ee_image, mask=mask, scale_refl=scale_refl)

    _gd_coll_name = ""  # geedim image collection name

    @staticmethod
    def _im_transform(ee_image):
        """ Optional type conversion to run after masking and scoring """
        return ee_image

    @property
    def gd_coll_name(self):
        """
        geedim collection name (landsat7_c2_l2|landsat8_c2_l2|sentinel2_toa|sentinel2_sr|modis_nbar)
        Returns
        -------
        : str
        """
        return self._gd_coll_name

    @property
    def masks(self):
        """
        Fill, cloud, shadow and validity masks

        Returns
        -------
        : dict
          A dictionary of ee.Image objects for each of the mask types
        """
        return self._masks

    @property
    def score(self):
        """
        Pixel quality score (distance to nearest cloud/shadow (m))

        Returns
        -------
        : ee.Image
        """
        return self._score

    @classmethod
    def ee_collection(cls):
        """
        Get the ee.ImageCollection corresponding to this image

        Returns
        -------
        : ee.ImageCollection
        """
        return ee.ImageCollection(info.gd_to_ee[cls._gd_coll_name])

    def _scale_refl(self, ee_image):
        """ Scale reflectance bands 0-10000 """
        return ee_image

    def _get_image_masks(self, ee_image):
        """
        Derive cloud, shadow, fill and validity masks for an image

        Parameters
        ----------
        ee_image : ee.Image
                   Derive masks for this image

        Returns
        -------
        masks : dict
                A dictionary of ee.Image objects for each of the mask types
        """
        # create constant masks for this base class
        masks = dict(
            cloud_mask=ee.Image(0).rename("CLOUD_MASK"),
            shadow_mask=ee.Image(0).rename("SHADOW_MASK"),
            fill_mask=ee.Image(1).rename("FILL_MASK"),
            valid_mask=ee.Image(1).rename("VALID_MASK"),
        )

        return masks

    # TODO: provide CLI access to cloud_dist
    def _get_image_score(self, ee_image, cloud_dist=5000, masks=None):
        """
        Get the cloud/shadow distance quality score for this image

        Parameters
        ----------
        ee_image : ee.Image
                   Find the score for this image
        cloud_dist : int, optional
                     The neighbourhood (m) in which to search for clouds (default: 5000)
        masks : dict, optional
                Existing masks as returned by _get_image_masks(...) (default: calculate the masks)
        Returns
        -------
        : ee.Image
          The cloud/shadow distance score as a single band image
        """
        radius = 1.5    # morphological pixel radius
        min_proj = get_projection(ee_image)     # projection corresponding to minimum scale band
        cloud_pix = ee.Number(cloud_dist).divide(min_proj.nominalScale()).toInt()   # cloud_dist in pixels
        if masks is None:
            masks = self._get_image_masks(ee_image)

        # combine cloud and shadow masks and morphologically open to remove small isolated patches
        cloud_shadow_mask = masks["cloud_mask"].Or(masks["shadow_mask"])
        cloud_shadow_mask = cloud_shadow_mask.focal_min(radius=radius).focal_max(radius=radius)

        # distance to nearest cloud/shadow (m)
        score = (
            cloud_shadow_mask.fastDistanceTransform(neighborhood=cloud_pix, units="pixels", metric="squared_euclidean")
            .sqrt()
            .multiply(min_proj.nominalScale())
            .rename("SCORE")
        )

        # clip score to cloud_dist and set to 0 in unfilled areas
        score = (score.unmask().
                 where(score.gt(ee.Image(cloud_dist)), cloud_dist).
                 where(masks["fill_mask"].unmask().Not(), 0))
        return score

    def _process_image(self, ee_image, mask=False, scale_refl=False, masks=None, score=None):
        """
        Add mask and score bands to a an Earth Engine image

        Parameters
        ----------
        ee_image : ee.Image
                   Earth engine image to add bands to
        mask : bool, optional
               Apply any validity mask to the image by setting nodata (default: False)
        scale_refl : bool, optional
                     Scale reflectance values from 0-10000 if they are not in that range already (default: False)

        Returns
        -------
        : ee.Image
          The processed image
        """
        if masks is None:
            masks = self._get_image_masks(ee_image)
        if score is None:
            score = self._get_image_score(ee_image, masks=masks)

        ee_image = ee_image.addBands(ee.Image(list(masks.values())), overwrite=True)
        ee_image = ee_image.addBands(score, overwrite=True)

        if mask:  # apply the validity mask to all bands (i.e. set those areas to nodata)
            ee_image = ee_image.updateMask(self._masks["valid_mask"])

        if scale_refl:  # scale reflectance range 0-10000
            ee_image = self._scale_refl(ee_image)

        return self._im_transform(ee_image)


class LandsatImage(MaskedImage):
    """ Base class for cloud/shadow masking and quality scoring landsat8_c2_l2 and landsat7_c2_l2 images """
    @staticmethod
    def _im_transform(ee_image):
        return ee.Image.toUint16(ee_image)

    def _get_image_masks(self, ee_image):
        # get cloud, shadow and fill masks from QA_PIXEL
        qa_pixel = ee_image.select("QA_PIXEL")
        cloud_mask = qa_pixel.bitwiseAnd((1 << 1) | (1 << 2) | (1 << 3)).neq(0).rename("CLOUD_MASK")
        shadow_mask = qa_pixel.bitwiseAnd(1 << 4).neq(0).rename("SHADOW_MASK")
        fill_mask = qa_pixel.bitwiseAnd(1).eq(0).rename("FILL_MASK")

        if self.gd_coll_name == "landsat8_c2_l2":
            # add landsat8 aerosol probability > medium to cloud mask
            # TODO: is SR_QA_AEROSOL helpful?
            sr_qa_aerosol = ee_image.select("SR_QA_AEROSOL")
            aerosol_prob = sr_qa_aerosol.rightShift(6).bitwiseAnd(3)
            aerosol_mask = aerosol_prob.gt(2).rename("AEROSOL_MASK")
            cloud_mask = cloud_mask.Or(aerosol_mask)

        # combine cloud, shadow and fill masks into validity mask
        valid_mask = ((cloud_mask.Or(shadow_mask)).Not()).And(fill_mask).rename("VALID_MASK")

        return dict(cloud_mask=cloud_mask, shadow_mask=shadow_mask, fill_mask=fill_mask, valid_mask=valid_mask)

    def _scale_refl(self, ee_image):
        # make lists of SR and non-SR band names
        all_bands = ee_image.bandNames()
        init_bands = ee.List([])
        def add_refl_bands(band, refl_bands):
            """ Server side function to add SR band names to a list """
            refl_bands = ee.Algorithms.If(
                ee.String(band).rindex("SR_B").eq(0), ee.List(refl_bands).add(band), refl_bands
            )
            return refl_bands

        sr_bands = ee.List(all_bands.iterate(add_refl_bands, init_bands))
        non_sr_bands = all_bands.removeAll(sr_bands)

        # scale to new range
        # low/high values from https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC08_C02_T1_L2?hl=en
        # TODO: what about scaling the BT_* surface temp band?  It has a different range.
        low = 0.2 / 2.75e-05
        high = low + 1 / 2.75e-05
        calib_image = ee_image.select(sr_bands).unitScale(low=low, high=high).multiply(10000.0)
        calib_image = calib_image.addBands(ee_image.select(non_sr_bands))
        calib_image = calib_image.updateMask(ee_image.mask())  # apply any existing mask to calib_image

        # copy system properties to calib_image
        for key in ["system:index", "system:id", "id", "system:time_start", "system:time_end"]:
            calib_image = calib_image.set(key, ee.String(ee_image.get(key)))

        # copy the rest of ee_image properties to calib_image and return
        return ee.Image(ee.Element(calib_image).copyProperties(ee.Element(ee_image)))


class Landsat8Image(LandsatImage):
    """ Class for cloud/shadow masking and quality scoring landsat8_c2_l2 images """
    _gd_coll_name = "landsat8_c2_l2"


class Landsat7Image(LandsatImage):
    """ Class for cloud/shadow masking and quality scoring landsat7_c2_l2 images """
    _gd_coll_name = "landsat7_c2_l2"


class Sentinel2Image(MaskedImage):
    """
    Base class for cloud masking and quality scoring sentinel2_sr and sentinel2_toa images
    (Does not use COPERNICUS/S2_CLOUD_PROBABILITY for cloud/shadow masking)
    """
    @staticmethod
    def _im_transform(ee_image):
        return ee.Image.toUint16(ee_image)

    def _get_image_masks(self, ee_image):
        masks = MaskedImage._get_image_masks(self, ee_image)    # get constant masks

        # derive cloud mask (only)
        qa = ee_image.select("QA60")    # bits 10 and 11 are opaque and cirrus clouds respectively
        cloud_mask = qa.bitwiseAnd((1 << 11) | (1 << 10)).neq(0).rename("CLOUD_MASK")

        # update validity and cloud masks
        valid_mask = cloud_mask.Not().rename("VALID_MASK")
        masks.update(cloud_mask=cloud_mask, valid_mask=valid_mask)
        return masks


class Sentinel2SrImage(Sentinel2Image):
    """
    Class for cloud masking and quality scoring sentinel2_sr images
    (Does not use COPERNICUS/S2_CLOUD_PROBABILITY for cloud/shadow masking)
    """
    _gd_coll_name = "sentinel2_sr"


class Sentinel2ToaImage(Sentinel2Image):
    """
    Class for cloud masking and quality scoring sentinel2_toa images
    (Does not use COPERNICUS/S2_CLOUD_PROBABILITY for cloud/shadow masking)
    """
    _gd_coll_name = "sentinel2_toa"


class Sentinel2ClImage(MaskedImage):
    """
    Base class for cloud/shadow masking and quality scoring sentinel2_sr and sentinel2_toa images
    (Uses COPERNICUS/S2_CLOUD_PROBABILITY to improve cloud/shadow masking)
    """
    def __init__(self, ee_image, mask=False, scale_refl=False):
        # TODO: provide CLI access to these attributes

        # set attributes before their use in __init__ below
        self._cloud_filter = 60  # Maximum image cloud cover percent allowed in image collection
        self._cloud_prob_thresh = 35  # Cloud probability (%); values greater than are considered cloud
        self._cloud_proj_dist = 1  # Maximum distance (km) to search for cloud shadows from cloud edges
        self._buffer = 100  # Distance (m) to dilate the edge of cloud-identified objects

        MaskedImage.__init__(self, ee_image, mask=mask, scale_refl=scale_refl)

    @staticmethod
    def _im_transform(ee_image):
        return ee.Image.toUint16(ee_image)

    @classmethod
    def from_id(cls, image_id, mask=False, scale_refl=False):
        # check image_id
        ee_coll_name = split_id(image_id)[0]
        if ee_coll_name not in info.ee_to_gd:
            raise ValueError(f"Unsupported collection: {ee_coll_name}")

        gd_coll_name = info.ee_to_gd[ee_coll_name]
        if gd_coll_name != cls._gd_coll_name:
            raise ValueError(
                f"{cls.__name__} only supports images from the {info.gd_to_ee[cls._gd_coll_name]} collection"
            )

        ee_image = ee.Image(image_id)

        # get cloud probability for ee_image and add as a band
        cloud_prob = ee.Image(f"COPERNICUS/S2_CLOUD_PROBABILITY/{split_id(image_id)[1]}")
        ee_image = ee_image.addBands(cloud_prob, overwrite=True)

        return cls(ee_image, mask=mask, scale_refl=scale_refl)

    def _get_image_masks(self, ee_image):
        """
        Derive cloud, shadow, fill and validity masks for an image, using the additional cloud probability band.
        Adapeted from https://developers.google.com/earth-engine/tutorials/community/sentinel-2-s2cloudless

        Parameters
        ----------
        ee_image : ee.Image
                   Derive masks for this image

        Returns
        -------
        masks : dict
                A dictionary of ee.Image objects for each of the mask types
        """

        masks = MaskedImage._get_image_masks(self, ee_image)    # get constant masks from base class

        # threshold the added cloud probability to get the initial cloud mask
        cloud_prob = ee_image.select("probability")
        cloud_mask = cloud_prob.gt(self._cloud_prob_thresh).rename("CLOUD_MASK")

        # TODO: dilate valid_mask by _buffer ?
        # TODO: does below work in N hemisphere?
        # See https://en.wikipedia.org/wiki/Solar_azimuth_angle

        # get solar azimuth
        shadow_azimuth = ee.Number(-90).add(ee.Number(ee_image.get("MEAN_SOLAR_AZIMUTH_ANGLE")))
        min_scale = get_projection(ee_image).nominalScale()

        # project the the cloud mask in the direction of shadows
        proj_dist_px = ee.Number(self._cloud_proj_dist * 1000).divide(min_scale)
        proj_cloud_mask = (
            cloud_mask.directionalDistanceTransform(shadow_azimuth, proj_dist_px)
            .select("distance")
            .mask()
            .rename("PROJ_CLOUD_MASK")
        )
        # .reproject(**{'crs': ee_image.select(0).projection(), 'scale': 100})

        if self.gd_coll_name == "sentinel2_sr":  # use SCL to reduce shadow_mask
            # Note: SCL does not classify cloud shadows well, they are often labelled "dark".  Instead of using only
            # cloud shadow areas from this band, we combine it with the projected dark and shadow areas from s2cloudless
            scl = ee_image.select("SCL")
            dark_shadow_mask = (
                scl.eq(3)
                .Or(scl.eq(2))
                .focal_min(self._buffer, "circle", "meters")
                .focal_max(self._buffer, "circle", "meters")
            )
            shadow_mask = proj_cloud_mask.And(dark_shadow_mask).rename("SHADOW_MASK")
        else:
            shadow_mask = proj_cloud_mask.rename("SHADOW_MASK")  # mask all areas that could be cloud shadow

        # combine cloud and shadow masks
        valid_mask = (cloud_mask.Or(shadow_mask)).Not().rename("VALID_MASK")
        masks.update(cloud_mask=cloud_mask, shadow_mask=shadow_mask, valid_mask=valid_mask)
        return masks

    @classmethod
    def ee_collection(cls):
        s2_sr_toa_col = ee.ImageCollection(info.gd_to_ee[cls._gd_coll_name])
        s2_cloudless_col = ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY")

        filter = ee.Filter.equals(leftField="system:index", rightField="system:index")
        inner_join = ee.ImageCollection(ee.Join.inner().apply(s2_sr_toa_col, s2_cloudless_col, filter))

        def map(feature):
            return ee.Image.cat(feature.get("primary"), feature.get("secondary"))

        return inner_join.map(map)


class Sentinel2SrClImage(Sentinel2ClImage):
    """
    Class for cloud/shadow masking and quality scoring sentinel2_sr images
    (Uses COPERNICUS/S2_CLOUD_PROBABILITY to improve cloud/shadow masking)
    """
    _gd_coll_name = "sentinel2_sr"


class Sentinel2ToaClImage(Sentinel2ClImage):
    """
    Class for cloud/shadow masking and quality scoring sentinel2_toa images
    (Uses COPERNICUS/S2_CLOUD_PROBABILITY to improve cloud/shadow masking)
    """
    _gd_coll_name = "sentinel2_toa"


class ModisNbarImage(MaskedImage):
    """
    Class for wrapping modis_nbar images.
    (These images are already cloud/shadow free composites, so the MaskedImage constant masks are used without further
    processing)
    """
    @staticmethod
    def _im_transform(ee_image):
        return ee.Image.toUint16(ee_image)

    _gd_coll_name = "modis_nbar"


##


def get_class(coll_name):
    """
    Get the ProcImage subclass for a specific collection

    Parameters
    ----------
    coll_name : str
                geedim or Earth Engine collection name to get class for
                (landsat7_c2_l2|landsat8_c2_l2|sentinel2_toa|sentinel2_sr|modis_nbar) or
                (LANDSAT/LE07/C02/T1_L2|LANDSAT/LC08/C02/T1_L2|COPERNICUS/S2|COPERNICUS/S2_SR|MODIS/006/MCD43A4)

    Returns
    -------
    : geedim.image.ProcImage
    """
    # TODO: populate this list by traversing the class heirarchy
    # TODO: allow coll_name = full image id
    # import inspect
    # from geedim import image
    # def find_subclasses():
    #     image_classes = {cls._gd_coll_name: cls for name, cls in inspect.getmembers(image)
    #                      if inspect.isclass(cls) and issubclass(cls, image.Image) and not cls is image.Image}
    #
    #     return image_classes

    gd_coll_name_map = dict(
        landsat7_c2_l2=Landsat7Image,
        landsat8_c2_l2=Landsat8Image,
        sentinel2_toa=Sentinel2ToaClImage,
        sentinel2_sr=Sentinel2SrClImage,
        modis_nbar=ModisNbarImage,
    )

    if split_id(coll_name)[0] in info.ee_to_gd:
        coll_name = split_id(coll_name)[0]

    if coll_name in gd_coll_name_map:
        return gd_coll_name_map[coll_name]
    elif coll_name in info.ee_to_gd:
        return gd_coll_name_map[info.ee_to_gd[coll_name]]
    else:
        raise ValueError(f"Unknown collection name: {coll_name}")


