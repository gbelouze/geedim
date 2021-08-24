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

import logging
from datetime import timedelta, datetime
import click

import ee
import pandas
import rasterio as rio
from rasterio.warp import transform_geom

from geedim import search, cli

# from shapely import geometry

##


'''
# from https://github.com/saveriofrancini/bap/blob/dbdf44df5cdf54cfb0d8eafaa7eeae68f0312467/js/library.js#L145 
var calculateCloudWeightAndDist = function(imageWithCloudMask, cloudDistMax){

  var cloudM = imageWithCloudMask.select('cloudM').unmask(0).eq(0);
  var nPixels = ee.Number(cloudDistMax).divide(30).toInt();
  var cloudDist = cloudM.fastDistanceTransform(nPixels, "pixels",  'squared_euclidean');
  // fastDistanceTransform max distance (i.e. 50*30 = 1500) is approzimate. Correcting it...
  cloudDist = cloudDist.where(cloudDist.gt(ee.Image(cloudDistMax)), cloudDistMax);
  
  var deltaCloud = ee.Image(1).toDouble() .divide((ee.Image(ee.Number(-0.008))
  .multiply(cloudDist.subtract(ee.Number(cloudDistMax/2)))).exp().add(1))
  .unmask(1)
  .select([0], ['cloudScore']);
  
  cloudDist = ee.Image(cloudDist).int16().rename('cloudDist');

  var keys = ['cloudScore', 'cloudDist'];
  var values = [deltaCloud, cloudDist]; 
  
  return ee.Dictionary.fromLists(keys, values);
};
exports.calculateCloudWeightAndDist = calculateCloudWeightAndDist;



        pjeDist = ee.Image().expression('1-exp((-dist+dmin)/(dmax*factor))',
                                        {
                                            'dist': distance,
                                            'dmin': dmini,
                                            'dmax': dmaxi,
                                            'factor': factori
                                        }).rename(bandname)

'''

'''
#from https://github.com/gee-community/gee_tools/blob/master/geetools/composite.py

def medoidScore(collection, bands=None, discard_zeros=False,
                bandname='sumdist', normalize=True):
    """ Compute a score to reflect 'how far' is from the medoid. Same params
     as medoid() """
    first_image = ee.Image(collection.first())
    if not bands:
        bands = first_image.bandNames()

    # Create a unique id property called 'enumeration'
    enumerated = tools.imagecollection.enumerateProperty(collection)
    collist = enumerated.toList(enumerated.size())

    def over_list(im):
        im = ee.Image(im)
        n = ee.Number(im.get('enumeration'))

        # Remove the current image from the collection
        filtered = tools.ee_list.removeIndex(collist, n)

        # Select bands for medoid
        to_process = im.select(bands)

        def over_collist(img):
            return ee.Image(img).select(bands)
        filtered = filtered.map(over_collist)

        # Compute the sum of the euclidean distance between the current image
        # and every image in the rest of the collection
        dist = algorithms.sumDistance(
            to_process, filtered,
            name=bandname,
            discard_zeros=discard_zeros)

        # Mask zero values
        if not normalize:
            # multiply by -1 to get the lowest value in the qualityMosaic
            dist = dist.multiply(-1)

        return im.addBands(dist)

    imlist = ee.List(collist.map(over_list))

    medcol = ee.ImageCollection.fromImages(imlist)

    # Normalize result to be between 0 and 1
    if normalize:
        min_sumdist = ee.Image(medcol.select(bandname).min())\
                        .rename('min_sumdist')
        max_sumdist = ee.Image(medcol.select(bandname).max()) \
                        .rename('max_sumdist')

        def to_normalize(img):
            sumdist = img.select(bandname)
            newband = ee.Image().expression(
                '1-((val-min)/(max-min))',
                {'val': sumdist,
                 'min': min_sumdist,
                 'max': max_sumdist}
            ).rename(bandname)
            return tools.image.replace(img, bandname, newband)

        medcol = medcol.map(to_normalize)

    return medcol


def medoid(collection, bands=None, discard_zeros=False):
    """ Medoid Composite. Adapted from https://www.mdpi.com/2072-4292/5/12/6481
    :param collection: the collection to composite
    :type collection: ee.ImageCollection
    :param bands: the bands to use for computation. The composite will include
        all bands
    :type bands: list
    :param discard_zeros: Masked and pixels with value zero will not be use
        for computation. Improves dark zones.
    :type discard_zeros: bool
    :return: the Medoid Composite
    :rtype: ee.Image
    """
    medcol = medoidScore(collection, bands, discard_zeros)
    comp = medcol.qualityMosaic('sumdist')
    final = tools.image.removeBands(comp, ['sumdist', 'mask'])
    return final
'''

#adapted from https://github.com/gee-community/gee_tools/blob/master/geetools/composite.py

def medoidScore(collection, bands=None, discard_zeros=False,
                bandname='sumdist', normalize=True):
    """ Compute a score to reflect 'how far' is from the medoid. Same params
     as medoid() """
    first_image = ee.Image(collection.first())
    if not bands:
        bands = first_image.bandNames()

    # Create a unique id property called 'enumeration'
    enumerated = tools.imagecollection.enumerateProperty(collection)
    collist = enumerated.toList(enumerated.size())

    def over_list(im):
        im = ee.Image(im)
        n = ee.Number(im.get('enumeration'))

        # Remove the current image from the collection
        filtered = tools.ee_list.removeIndex(collist, n)

        # Select bands for medoid
        to_process = im.select(bands)

        def over_collist(img):
            return ee.Image(img).select(bands)
        filtered = filtered.map(over_collist)

        # Compute the sum of the euclidean distance between the current image
        # and every image in the rest of the collection
        dist = algorithms.sumDistance(
            to_process, filtered,
            name=bandname,
            discard_zeros=discard_zeros)

        # Mask zero values
        if not normalize:
            # multiply by -1 to get the lowest value in the qualityMosaic
            dist = dist.multiply(-1)

        return im.addBands(dist)

    imlist = ee.List(collist.map(over_list))

    medcol = ee.ImageCollection.fromImages(imlist)

    # Normalize result to be between 0 and 1
    if normalize:
        min_sumdist = ee.Image(medcol.select(bandname).min())\
                        .rename('min_sumdist')
        max_sumdist = ee.Image(medcol.select(bandname).max()) \
                        .rename('max_sumdist')

        def to_normalize(img):
            sumdist = img.select(bandname)
            newband = ee.Image().expression(
                '1-((val-min)/(max-min))',
                {'val': sumdist,
                 'min': min_sumdist,
                 'max': max_sumdist}
            ).rename(bandname)
            return tools.image.replace(img, bandname, newband)

        medcol = medcol.map(to_normalize)

    return medcol


def medoid(collection, bands=None, discard_zeros=False):
    """ Medoid Composite. Adapted from https://www.mdpi.com/2072-4292/5/12/6481
    :param collection: the collection to composite
    :type collection: ee.ImageCollection
    :param bands: the bands to use for computation. The composite will include
        all bands
    :type bands: list
    :param discard_zeros: Masked and pixels with value zero will not be use
        for computation. Improves dark zones.
    :type discard_zeros: bool
    :return: the Medoid Composite
    :rtype: ee.Image
    """
    medcol = medoidScore(collection, bands, discard_zeros)
    comp = medcol.qualityMosaic('sumdist')
    final = tools.image.removeBands(comp, ['sumdist', 'mask'])
    return final

'''
    # set metadata to indicate component images
    return comp_im.set('COMPOSITE_IMAGES', self._im_df[['ID', 'DATE'] + self._im_props].to_string()).toUint16()
'''


def collection_from_ids(ids, apply_mask=False, add_aux_bands=False, scale_refl=False):
    """
    Create ee.ImageCollection of masked and scored images, from a list of EE image IDs

    Parameters
    ----------
    ids : list[str]
          list of EE image IDs
    apply_mask : bool, optional
                 Apply any validity mask to the image by setting nodata (default: False)
    add_aux_bands: bool, optional
                   Add auxiliary bands (cloud, shadow, fill & validity masks, and quality score) (default: False)
    scale_refl : bool, optional
                 Scale reflectance values from 0-10000 if they are not in that range already (default: True)

    Returns
    -------
    : ee.ImageCollection
    """

    collection_info = search.load_collection_info()
    ee_geedim_map = dict([(v['ee_collection'], k) for k, v in collection_info.items()])
    id_collection = '/'.join(ids[0].split('/')[:-1])

    if not id_collection in ee_geedim_map.keys():
        raise ValueError(f'Unsupported collection: {id_collection}')

    id_check = ['/'.join(im_id.split('/')[:-1]) == id_collection for im_id in ids[1:]]
    if not all(id_check):
        raise ValueError(f'All IDs must belong to the same collection')

    im_collection = cli.cls_col_map[ee_geedim_map[id_collection]](collection=ee_geedim_map[id_collection])

    im_list = ee.List([])
    for im_id in ids:
        im = im_collection.get_image(im_id, apply_mask=apply_mask, add_aux_bands=add_aux_bands, scale_refl=scale_refl)
        im_list = im_list.add(im)

    return ee.ImageCollection(im_list)


def composite(images, method='q_mosaic', apply_mask=True):
    # qualityMosaic will prefer clear pixels based on SCORE and irrespective of mask, for other methods, the mask
    # is needed to avoid including cloudy pixels
    method = str(method).lower()

    if method != 'q_mosaic' and apply_mask == False:
        apply_mask = True   #

    ee_im_collection = None
    if isinstance(images, list) and len(images) > 0:
        if isinstance(images[0], str):
            ee_im_collection = collection_from_ids(images, apply_mask=apply_mask, add_aux_bands=True)
        elif isinstance(images[0], ee.Image):
            im_list = ee.List([])
            for image in images:
                im_list = im_list.add(image)
            ee_im_collection = ee.ImageCollection(im_list)
    elif isinstance(images, ee.ImageCollection):
        ee_im_collection = images

    if ee_im_collection is None:
        raise ValueError(f'Unsupported images parameter format: {type(images)}')

    if method == 'q_mosaic':
        comp_image = ee_im_collection.qualityMosaic('SCORE')
    elif method == 'mosaic':
        comp_image = ee_im_collection.mosaic()
    elif method == 'median':
        comp_image = ee_im_collection.median()
    else:
        raise ValueError(f'Unsupported composite method: {method}')

    # comp_image.set('COMPOSITE_IMAGES', self._im_df[['ID', 'DATE'] + self._im_props].to_string()).toUint16()
    return comp_image
