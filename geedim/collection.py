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
# Functionality for searching and compositing EE image collections
import collections
from datetime import datetime, timedelta

import ee
import pandas as pd

import geedim.image
from geedim import image, info, medoid


##
class Collection(object):
    def __init__(self, gd_coll_name):
        """
        Class for searching and compositing an EE image collection

        Parameters
        ----------
        gd_coll_name : str
                       geedim collection name:(landsat7_c2_l2|landsat8_c2_l2|sentinel2_toa|sentinel2_sr|modis_nbar)
        """
        if gd_coll_name not in info.gd_to_ee:
            raise ValueError(f"Unsupported collection: {gd_coll_name}")

        self._gd_coll_name = gd_coll_name
        self._ee_coll_name = info.gd_to_ee[self._gd_coll_name]
        self._collection_info = info.collection_info[gd_coll_name]
        self._image_class = image.get_class(gd_coll_name)  # geedim.image.*Image class for this collection
        self._ee_collection = None  # the wrapped ee.ImageCollection

        self._summary_key_df = pd.DataFrame(self._collection_info["properties"])  # key to metadata summary
        self._summary_df = None  # summary of the image metadata

    @classmethod
    def from_ids(cls, image_ids, mask=False, scale_refl=False):
        """
        Create collection from image IDs

        Parameters
        ----------
        image_ids : list(str)
                    A list of the EE image IDs (should all be from same collection)
        mask : bool, optional
               Apply a validity (cloud & shadow) mask to the image (default: False)
        scale_refl : bool, optional
                     Scale reflectance bands 0-10000 if they are not in that range already (default: False)

        Returns
        -------
        : geedim.collection.Collection
        """
        # check image IDs are valid
        ee_coll_name = image.split_id(image_ids[0])[0]
        if ee_coll_name not in info.ee_to_gd:
            raise ValueError(f"Unsupported collection: {ee_coll_name}")

        id_check = [image.split_id(im_id)[0] == ee_coll_name for im_id in image_ids[1:]]
        if not all(id_check):
            raise ValueError(f"All images must belong to the same collection")

        # create the collection object
        gd_coll_name = info.ee_to_gd[ee_coll_name]
        gd_collection = cls(gd_coll_name)

        # build and wrap an ee.ImageCollection of processed (masked and scored) images
        im_list = ee.List([])
        for im_id in image_ids:
            gd_image = gd_collection._image_class.from_id(im_id, mask=mask, scale_refl=scale_refl)
            im_list = im_list.add(gd_image.ee_image)

        gd_collection._ee_collection = ee.ImageCollection(im_list)
        return gd_collection

    @property
    def ee_collection(self):
        """
         The underlying ee.ImageCollection

        Returns
        -------
        : ee.ImageCollection
        """
        return self._ee_collection

    @property
    def summary_key_df(self):
        """
        A key to Collection.summary_df
        (pandas.DataFrame with ABBREV and DESCRIPTION columns, and rows corresponding columns in summary_df)

        Returns
        -------
        : pandas.DataFrame
        """
        return self._summary_key_df

    @property
    def summary_df(self):
        """
        Summary of collection image metadata (pandas.DataFrame with a row for each image)

        Returns
        -------
        : pandas.DataFrame
        """
        if self._summary_df is None:
            self._summary_df = self._get_summary_df(self._ee_collection)
        return self._summary_df

    @property
    def summary_key(self):
        """
        Formatted string of Collection.summary_key_df

        Returns
        -------
        : str
        """
        return self._summary_key_df[["ABBREV", "DESCRIPTION"]].to_string(index=False, justify="right")

    @property
    def summary(self):
        """
        Formatted string of Collection.summary_df

        Returns
        -------
        : str
        """
        return self.summary_df.to_string(
            float_format="%.2f",
            formatters={"DATE": lambda x: datetime.strftime(x, "%Y-%m-%d %H:%M")},
            columns=self._summary_key_df.ABBREV,
            index=False,
            justify="center",
        )

    composite_methods = ["q_mosaic", "mosaic", "median", "medoid"]  # supported composite methods

    def search(self, start_date, end_date, region, valid_portion=0, mask=False, scale_refl=False):
        """
        Search for images based on date, region etc criteria

        Parameters
        ----------
        start_date : datetime.datetime
                     Start image capture date
        end_date : datetime.datetime
                   End image capture date (if None, then set to start_date + 1 day)
        region : dict, geojson, ee.Geometry
                 Polygon in WGS84 specifying a region that images should intersect
        valid_portion: int, optional
                       Minimum portion (%) of image pixels that should be valid (not cloud/shadow)
        mask : bool, optional
               Apply a validity (cloud & shadow) masks to images.  Relevant only if search results will be
                composited (default: False)
        scale_refl : bool, optional
                     Scale reflectance bands 0-10000 if they are not in that range already.  Relevant only if search
                     results will be composited  (default: False)

        Returns
        -------
        image_df : pandas.DataFrame
                   Dataframe specifying image properties that match the search criteria
        """
        # Initialise
        if end_date is None:
            end_date = start_date + timedelta(days=1)
        if end_date <= start_date:
            raise ValueError("`end_date` must be at least a day later than `start_date`")

        def calc_stats(ee_image):
            """ Server side calculation of validity and score stats within region of interest """
            max_scale = geedim.image.get_projection(ee_image, min=False).nominalScale()
            gd_image = self._image_class(ee_image, mask=mask, scale_refl=scale_refl)

            stats = (
                ee.Image([gd_image.masks["valid_mask"], gd_image.score])
                .unmask()
                .reduceRegion(reducer="mean", geometry=region, scale=max_scale)
                .rename(["VALID_MASK", "SCORE"], ["VALID_PORTION", "AVG_SCORE"])
            )

            stats = stats.set("VALID_PORTION", ee.Number(stats.get("VALID_PORTION")).multiply(100))
            return gd_image.ee_image.set(stats)

        try:
            # filter the image collection, finding cloud/shadow masks, and region stats
            self._ee_collection = (
                self._image_class.ee_collection()
                .filterDate(start_date, end_date)
                .filterBounds(region)
                .map(calc_stats)
                .filter(ee.Filter.gt("VALID_PORTION", valid_portion))
            )
        finally:
            # update summary_df with image metadata from the filtered collection
            self._summary_df = self._get_summary_df(self._ee_collection)

        return self._summary_df

    def composite(self, method="q_mosaic"):
        """
        Create a cloud/shadow free composite.

        Note: composite() can be called on a filtered collection created by search(..), or on a collection created with
              fromIds(...)
              The `mask` parameter in search(...) and fromId(...) affects the composite and should generally be
              True so that cloud/shadow pixels are excluded.

        Parameters
        ----------
        method : str, optional
                 Compositing method to use (q_mosaic|mosaic|median|medoid).  (Default: q_mosaic).

        Returns
        -------
        : (ee.Image, str)
          The composite image, composite image ID
        """
        method = str(method).lower()

        if method == "q_mosaic":
            comp_image = self._ee_collection.qualityMosaic("SCORE")
        elif method == "mosaic":
            comp_image = self._ee_collection.mosaic()
        elif method == "median":
            comp_image = self._ee_collection.median()
            # median creates float images, so re-apply any type conversion
            comp_image = self._image_class._im_transform(comp_image)
        elif method == "medoid":
            # limit medoid to surface reflectance bands
            sr_bands = [band_dict["id"] for band_dict in self._collection_info["bands"]]
            comp_image = medoid.medoid(self._ee_collection, bands=sr_bands)
        else:
            raise ValueError(f"Unsupported composite method: {method}")

        # populate image metadata with info on component images
        comp_image = comp_image.set("COMPONENT_IMAGES", self.summary)

        # construct an ID for the composite
        start_date = self.summary_df.DATE.iloc[0].strftime("%Y_%m_%d")
        end_date = self.summary_df.DATE.iloc[-1].strftime("%Y_%m_%d")

        comp_id = f"{self._ee_coll_name}/{start_date}-{end_date}-{method.upper()}_COMP"
        comp_image = comp_image.set("system:id", comp_id)

        CompositeResult = collections.namedtuple("CompositeResult", ["image", "id"])
        return CompositeResult(comp_image, comp_id)

    def _get_summary_df(self, ee_collection):
        """
        Retrieve a summary of collection image metadata.

        Parameters
        ----------
        ee_collection : ee.ImageCollection
                        Filtered image collection whose image metadata to retrieve

        Returns
        -------
        : pandas.DataFrame
        pandas.DataFrame with a row of metadata for each image)
        """

        if ee_collection is None:
            return pd.DataFrame([], columns=self._summary_key_df.ABBREV)  # return empty dataframe

        # server side aggregation of relevant properties of ee_collection images
        init_list = ee.List([])

        def aggregrate_props(ee_image, prop_list):
            prop = ee.Dictionary()
            for prop_key in self._summary_key_df.PROPERTY.values:
                prop = prop.set(
                    prop_key, ee.Algorithms.If(ee_image.get(prop_key), ee_image.get(prop_key), ee.String("None"))
                )
            return ee.List(prop_list).add(prop)

        # retrieve list of dicts of collection image properties (the only call to getInfo() in Collection)
        im_prop_list = ee.List(ee_collection.iterate(aggregrate_props, init_list)).getInfo()

        if len(im_prop_list) == 0:
            return pd.DataFrame([], columns=self._summary_key_df.ABBREV)  # return empty dataframe

        # Convert ee.Date to python datetime
        for i, prop_dict in enumerate(im_prop_list):
            if "system:time_start" in prop_dict:
                prop_dict["system:time_start"] = datetime.utcfromtimestamp(prop_dict["system:time_start"] / 1000)

        # convert property list to DataFrame
        im_prop_df = pd.DataFrame(im_prop_list, columns=im_prop_list[0].keys())
        im_prop_df = im_prop_df.sort_values(by="system:time_start").reset_index(drop=True)  # sort by acquisition time
        im_prop_df = im_prop_df.rename(
            columns=dict(zip(self._summary_key_df.PROPERTY, self._summary_key_df.ABBREV))
        )  # abbreviate column names
        im_prop_df = im_prop_df[self._summary_key_df.ABBREV.to_list()]  # reorder columns

        return im_prop_df