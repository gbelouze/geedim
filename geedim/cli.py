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
import json
import logging
import os
import pathlib
import sys
from types import SimpleNamespace

import click
import rasterio.crs as rio_crs
from rasterio.dtypes import dtype_ranges
from rasterio.errors import CRSError

from geedim import collection as coll_api
from geedim import info, masked_image, _ee_init, parse_image_list, version, image_from_id
from geedim.collection import BaseCollection, MaskedCollection
from geedim.image import BaseImage, split_id, get_bounds

logger = logging.getLogger(__name__)


class PlainInfoFormatter(logging.Formatter):
    """logging formatter to format INFO logs without the module name etc prefix"""

    def format(self, record):
        if record.levelno == logging.INFO:
            self._style._fmt = "%(message)s"
        else:
            self._style._fmt = "%(levelname)s:%(name)s: %(message)s"
        return super().format(record)


class ChainedCommand(click.Command):
    """
    click Command class for managing parameters shared between chained commands,
    and formatting single newlines in help strings as single newlines.
    """

    def get_help(self, ctx):
        """Format help strings with single newlines as single newlines."""
        # adapted from https://stackoverflow.com/questions/55585564/python-click-formatting-help-text
        orig_wrap_test = click.formatting.wrap_text

        def wrap_text(text, width, **kwargs):
            text = text.replace('\n', '\n\n')
            return orig_wrap_test(text, width, **kwargs).replace('\n\n', '\n')

        click.formatting.wrap_text = wrap_text
        return click.Command.get_help(self, ctx)

    def invoke(self, ctx):
        """Manage shared `image_list` and `region` parameters."""

        # combine `region` and `bbox` into a single region in the context object
        region = ctx.params['region'] if 'region' in ctx.params else None
        bbox = ctx.params['bbox'] if 'bbox' in ctx.params else None
        region = region or bbox
        if not region is None:
            ctx.obj.region = region

        if 'image_id' in ctx.params:
            # append any image id's to the image_list
            ctx.obj.image_list += list(ctx.params['image_id'])

        return click.Command.invoke(self, ctx)


def _configure_logging(verbosity):
    """configure python logging level"""
    # adapted from rasterio https://github.com/rasterio/rasterio
    log_level = max(10, 20 - 10 * verbosity)

    # limit logging config to homonim by applying to package logger, rather than root logger
    # pkg_logger level etc are then 'inherited' by logger = getLogger(__name__) in the modules
    pkg_logger = logging.getLogger(__package__)
    formatter = PlainInfoFormatter()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(log_level)
    logging.captureWarnings(True)


def _collection_cb(ctx, param, value):
    """click callback to validate collection name"""
    if value in info.gd_to_ee:
        value = info.gd_to_ee[value]
    return value


def _crs_cb(ctx, param, crs):
    """click callback to validate and parse the CRS."""
    if crs is not None:
        try:
            wkt_fn = pathlib.Path(crs)
            if wkt_fn.exists():  # read WKT from file, if it exists
                with open(wkt_fn, 'r') as f:
                    crs = f.read()

            crs = rio_crs.CRS.from_string(crs).to_wkt()
        except CRSError as ex:
            raise click.BadParameter(f'Invalid CRS value: {crs}.\n {str(ex)}', param=param)
    return crs


def _bbox_cb(ctx, param, value):
    """click callback to validate and parse --bbox"""
    if value is None or len(value) == 0 or isinstance(value, dict):
        pass
    elif isinstance(value, tuple) and len(value) == 4:  # --bbox
        xmin, ymin, xmax, ymax = value
        coordinates = [[xmax, ymax], [xmax, ymin], [xmin, ymin], [xmin, ymax], [xmax, ymax]]
        value = dict(type='Polygon', coordinates=[coordinates])
    else:
        raise click.BadParameter(f'Invalid bbox: {value}.', param=param)
    return value


def _region_cb(ctx, param, value):
    """click callback to validate and parse --region"""
    if value is None or len(value) == 0 or isinstance(value, dict):
        pass
    elif isinstance(value, str):  # read region file/string
        filename = pathlib.Path(value)
        if not filename.exists():
            raise click.BadParameter(f'{filename} does not exist.', param=param)
        if 'json' in filename.suffix:
            with open(filename) as f:
                value = json.load(f)
        else:
            value, _ = get_bounds(value, expand=10)
    else:
        raise click.BadParameter(f'Invalid region: {value}.', param=param)
    return value


def _parse_image_list(obj: SimpleNamespace, mask=False, cloud_dist=False):
    """Validate and prepare the obj.image_list for export/download."""
    if len(obj.image_list) == 0:
        raise click.BadOptionUsage('image_id',
                                   'Either pass --id, or chain this command with a successful `search` or `composite`')
    image_list = parse_image_list(obj.image_list, mask=mask, cloud_dist=cloud_dist)
    if obj.region is None and any([not im.has_fixed_projection for im in image_list]):
        raise click.BadOptionUsage('region', 'One of --region or --box is required for a composite image.')
    return image_list


def _collection_from_list(image_list, **kwargs):
    """Return a Base/MaskedCollection from a list of image ID's and/or Base/MaskedImage objects."""
    ee_image_list = []
    masked = []
    for image_obj in image_list:
        if isinstance(image_obj, str):
            ee_coll_name = split_id(image_obj)[0]
            ee_image_list.append(image_from_id(image_obj, **kwargs).ee_image)
            masked.append(ee_coll_name in info.collection_info)
        elif isinstance(image_obj, BaseImage):
            ee_image_list.append(image_obj.ee_image)
            masked.append(type(image_obj) != BaseImage)  # i.e. it is derived from BaseImage, but not BaseImage itself
        else:
            raise TypeError(f'Unknown image object type: {type(image_obj)}')

    return MaskedCollection.from_ee_list(ee_image_list) if all(masked) else BaseCollection.from_ee_list(ee_image_list)


""" Define click options that are common to more than one command """
bbox_option = click.option(
    "-b",
    "--bbox",
    type=click.FLOAT,
    nargs=4,
    help="Region defined by bounding box co-ordinates in WGS84 (xmin, ymin, xmax, ymax).",
    required=False,
    default=None,
    callback=_bbox_cb,
)
region_option = click.option(
    "-r",
    "--region",
    type=click.Path(exists=True, dir_okay=False),
    help="Region defined by geojson or raster file.",
    required=False,
    default=None,
    callback=_region_cb,
)
image_id_option = click.option(
    "-i",
    "--id",
    "image_id",
    type=click.STRING,
    help="Earth engine image ID(s).",
    required=False,
    multiple=True,
)
crs_option = click.option(
    "-c",
    "--crs",
    type=click.STRING,
    default=None,
    help="Reproject image(s) to this CRS (EPSG string or path to text file containing WKT). \n[default: source CRS]",
    required=False,
    callback=_crs_cb,
)
scale_option = click.option(
    "-s",
    "--scale",
    type=click.FLOAT,
    default=None,
    help="Resample image bands to this pixel resolution (m). \n[default: minimum of the source band resolutions]",
    required=False,
)
dtype_option = click.option(
    "-dt",
    "--dtype",
    type=click.Choice(list(dtype_ranges.keys()), case_sensitive=False),
    default=None,
    help="Convert image(s) to this data type.",
    required=False,
)
mask_option = click.option(
    "-m/-nm",
    "--mask/--no-mask",
    default=masked_image.MaskedImage._default_params['mask'],
    help="Do/don't apply (cloud and shadow) nodata mask(s).  [default: --no-mask]",
    required=False,
)
resampling_option = click.option(
    "-rs",
    "--resampling",
    type=click.Choice(["near", "bilinear", "bicubic"], case_sensitive=True),
    help="Resampling method.",
    default="near",
    show_default=True,
)
cloud_dist_option = click.option(
    "-cd",
    "--cloud-dist",
    type=click.FLOAT,
    default=masked_image.MaskedImage._default_params['cloud_dist'],
    help="Search for cloud/shadow inside this radius (m) to determine compositing quality score.",
    show_default=True,
    required=False,
)


# Define the geedim CLI and chained command group
@click.group(chain=True)
@click.option(
    '--verbose', '-v',
    count=True,
    help="Increase verbosity.")
@click.option(
    '--quiet', '-q',
    count=True,
    help="Decrease verbosity.")
@click.version_option(version=version.__version__, message="%(version)s")
@click.pass_context
def cli(ctx, verbose, quiet):
    ctx.obj = SimpleNamespace(image_list=[], region=None)
    verbosity = verbose - quiet
    _configure_logging(verbosity)
    _ee_init()


# Define search command options
@click.command(cls=ChainedCommand)
@click.option(
    "-c",
    "--collection",
    type=click.STRING,  # click.Choice(list(info.gd_to_ee.keys())[:-1], case_sensitive=False),
    help="Earth Engine image collection to search.",
    default="landsat8_c2_l2",
    show_default=True,
    callback=_collection_cb,
)
@click.option(
    "-s",
    "--start-date",
    type=click.DateTime(),
    help="Start date (UTC).",
    required=True,
)
@click.option(
    "-e",
    "--end-date",
    type=click.DateTime(),
    help="End date (UTC).  \n[default: start_date + 1 day]",
    required=False,
)
@bbox_option
@click.option(
    "-r",
    "--region",
    type=click.Path(exists=True, dir_okay=False),
    help="Region defined by geojson or raster file. [One of --bbox or --region is required]",
    required=False,
    callback=_region_cb,
)
@click.option(
    "-vp",
    "--valid-portion",
    type=click.FloatRange(min=0, max=100),
    default=0,
    help="Lower limit of the portion of valid (cloud and shadow free) pixels (%).",
    required=False,
    show_default=True,
)
@click.option(
    "-o",
    "--output",
    type=click.Path(exists=False, dir_okay=False, writable=True),
    default=None,
    help="Write results to this filename, file type inferred from extension: [.csv|.json]",
    required=False,
)
@click.pass_obj
def search(obj, collection, start_date, end_date, bbox, region, valid_portion, output):
    """ Search for images """
    # TODO: what about chaining search with search, or after composite.  Unlikely use case, but if possible
    #  would be neat to structure the sw in this way e.g. if the image_list is not empty, then make the collection
    #  out of that.

    if not obj.region:
        raise click.BadOptionUsage('region', 'Either pass --region or --bbox')

    logger.info(f'\nSearching for {collection} images between '
                f'{start_date.strftime("%Y-%m-%d")} and {end_date.strftime("%Y-%m-%d")}...')

    # create collection wrapper and search
    if collection in info.ee_to_gd:
        gd_collection = coll_api.MaskedCollection(collection)
        im_df = gd_collection.search(start_date, end_date, obj.region, valid_portion=valid_portion)
    else:
        gd_collection = coll_api.BaseCollection(collection)
        im_df = gd_collection.search(start_date, end_date, obj.region)

    if im_df.shape[0] == 0:
        logger.info('No images found\n')
    else:
        obj.image_list += im_df.ID.values.tolist()  # store ids for chaining
        logger.info(f'{im_df.shape[0]} images found\n')
        logger.info(f'Image property descriptions:\n\n{gd_collection.summary_key}\n')
        logger.info(f'Search Results:\n\n{gd_collection.summary}')

    # write results to file
    if output is not None:
        output = pathlib.Path(output)
        if output.suffix == '.csv':
            im_df.to_csv(output, index=False)
        elif output.suffix == '.json':
            im_df.to_json(output, orient='index')
        else:
            raise ValueError(f'Unknown output file extension: {output.suffix}')


cli.add_command(search)


# Define download command options
@click.command(cls=ChainedCommand)
@image_id_option
@bbox_option
@region_option
@click.option(
    "-dd",
    "--download-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True),
    default=os.getcwd(),
    help="Download image file(s) to this directory.  [default: cwd]",
    required=False,
)
@crs_option
@scale_option
@dtype_option
@mask_option
@resampling_option
@cloud_dist_option
@click.option(
    "-o",
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite the destination file if it exists.  [default: prompt the user for confirmation]",
    required=False,
    show_default=False,
)
@click.pass_obj
def download(obj, image_id, bbox, region, download_dir, mask, cloud_dist, overwrite, **kwargs):
    """Download image(s), without size limits and including metadata, and with optional cloud and shadow masking."""
    logger.info('\nDownloading:\n')
    image_list = _parse_image_list(obj, mask=mask, cloud_dist=cloud_dist)
    for im in image_list:
        filename = pathlib.Path(download_dir).joinpath(im.name + '.tif')
        im.download(filename, overwrite=overwrite, region=obj.region, **kwargs)


cli.add_command(download)


# Define export command options
@click.command(cls=ChainedCommand)
@image_id_option
@bbox_option
@region_option
@click.option(
    "-df",
    "--drive-folder",
    type=click.STRING,
    default='',
    help="Export image(s) to this Google Drive folder. [default: root]",
    required=False,
)
@crs_option
@scale_option
@dtype_option
@mask_option
@resampling_option
@cloud_dist_option
@click.option(
    "-w/-nw",
    "--wait/--no-wait",
    default=True,
    help="Wait / don't wait for export to complete.  [default: --wait]",
    required=False,
)
@click.pass_obj
def export(obj, image_id, bbox, region, drive_folder, mask, cloud_dist, wait, **kwargs):
    """ Export image(s) to Google Drive, with optional cloud and shadow masking """
    logger.info('\nExporting:\n')
    image_list = _parse_image_list(obj, mask=mask, cloud_dist=cloud_dist)
    export_tasks = []
    for im in image_list:
        task = im.export(im.name, folder=drive_folder, wait=False, region=obj.region, **kwargs)
        export_tasks.append(task)
        logger.info(f'Started {im.name}') if not wait else None

    if wait:
        for task in export_tasks:
            BaseImage.monitor_export_task(task)


cli.add_command(export)


# Define composite command options
@click.command(cls=ChainedCommand)
@image_id_option
@click.option(
    "-cm",
    "--method",
    type=click.Choice(coll_api.MaskedCollection.composite_methods, case_sensitive=False),
    help="Compositing method to use.",
    default="q_mosaic",
    show_default=True,
    required=False,
)
@click.option(
    "-m/-nm",
    "--mask/--no-mask",
    default=True,
    help="Do/don't apply (cloud and shadow) nodata mask(s) before compositing.  [default: --mask]",
    required=False,
)
@resampling_option
@cloud_dist_option
@click.pass_obj
def composite(obj, image_id, mask, method, resampling, cloud_dist):
    """ Create a cloud-free composite image """

    # get image ids from command line or chained search command
    if len(obj.image_list) == 0:
        raise click.BadOptionUsage('image_id', 'Either pass --id, or chain this command with a successful `search`')

    gd_collection = _collection_from_list(obj.image_list, mask=mask, cloud_dist=cloud_dist)
    obj.image_list = [gd_collection.composite(method=method, resampling=resampling)]


cli.add_command(composite)

##
