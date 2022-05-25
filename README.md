[comment]: <> ([![Publish]&#40;https://github.com/dugalh/geedim/actions/workflows/publish-pypi.yml/badge.svg&#41;]&#40;https://github.com/dugalh/geedim/actions/workflows/publish-pypi.yml&#41;)
[![Tests](https://github.com/dugalh/geedim/actions/workflows/run-unit-tests.yml/badge.svg)](https://github.com/dugalh/geedim/actions/workflows/run-unit-tests.yml)
[![codecov](https://codecov.io/gh/dugalh/geedim/branch/main/graph/badge.svg?token=69GZNQ3TI3)](https://codecov.io/gh/dugalh/geedim)
[![PyPI version](https://badge.fury.io/py/geedim.svg)](https://badge.fury.io/py/geedim)
[![Anaconda-Server Badge](https://anaconda.org/conda-forge/geedim/badges/version.svg)](https://anaconda.org/conda-forge/geedim)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

# `geedim`
Search, composite, and download [Google Earth Engine](https://earthengine.google.com/) imagery, without size limits. 

## Description
`geedim` provides a command line interface and API for searching, compositing and downloading satellite imagery from Google Earth Engine (EE).  It optionally performs cloud/shadow masking, and cloud/shadow-free compositing on supported collections.  Images and composites can be downloaded, or exported to Google Drive.  Images larger than the EE size limit are split and downloaded as separate tiles, then re-assembled into a single GeoTIFF.   

### Supported imagery
Any EE imagery can be searched, composited or downloaded by `geedim`.  Cloud / shadow masking is supported for on the following collections:

`geedim` name | EE name| Description
---------|-----------|------------
landsat4_c2_l2 | [LANDSAT/LT04/C02/T1_L2](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LT04_C02_T1_L2) | Landsat 4, collection 2, tier 1, level 2 surface reflectance 
landsat5_c2_l2 | [LANDSAT/LT05/C02/T1_L2](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LT05_C02_T1_L2) | Landsat 5, collection 2, tier 1, level 2 surface reflectance 
landsat7_c2_l2 | [LANDSAT/LE07/C02/T1_L2](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LE07_C02_T1_L2) | Landsat 7, collection 2, tier 1, level 2 surface reflectance 
landsat8_c2_l2 | [LANDSAT/LC08/C02/T1_L2](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC08_C02_T1_L2) | Landsat 8, collection 2, tier 1, level 2 surface reflectance 
landsat9_c2_l2 | [LANDSAT/LC09/C02/T1_L2](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC09_C02_T1_L2) | Landsat 9, collection 2, tier 1, level 2 surface reflectance 
sentinel2_toa | [COPERNICUS/S2](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2) | Sentinel-2, level 1C, top of atmosphere reflectance 
sentinel2_sr | [COPERNICUS/S2_SR](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR) | Sentinel-2, level 2A, surface reflectance

## Requirements
`geedim` is a python 3 library, and requires users to be registered with [Google Earth Engine](https://signup.earthengine.google.com).

## Installation
`geedim` is available via `pip` and `conda`.  Under Windows, using `conda` is the easiest way to resolve binary dependencies. 
### conda
The [Miniconda](https://docs.conda.io/en/latest/miniconda.html) installation provides a minimal `conda`.
```shell
conda install -c conda-forge geedim
```
### pip
```shell
pip install geedim
```

Following installation, Earth Engine should be authenticated:  
```shell
earthengine authenticate
```

## Getting started
### Command line interface
`geedim` command line functionality is accessed through the commands:
* `search`: Search for images.
* `composite`: Create a composite image.
* `download`: Download image(s).
* `export`: Export image(s) to Google Drive.
* `config`: Configure cloud/shadow masking.

Get help on `geedim` with:
```
geedim --help
```
and help on a `geedim` command with:
```
geedim <command> --help
```

#### Examples
Search for Landsat-8 images.
```shell
geedim search -c landsat8_c2_l2 -s 2021-06-01 -e 2021-07-01 --bbox 24 -33 24.1 -33.1
```
Download a Landsat-8 image with cloud / shadow mask applied.
```shell
geedim download -i LANDSAT/LC08/C02/T1_L2/LC08_172083_20210610 --bbox 24 -33 24.1 -33.1 --mask
```

### Command pipelines
Multiple `geedim` commands can be chained together in a pipeline where  image results from the previous command form inputs to the current command.  For example, if the `composite` command is chained with `download` command, the created composite image will be downloaded, or if the `search` command is chained with the `composite` command, the search result images will be composited.  

Common command options are also piped between chained commands.  For example, if the `config` command is chained with other commands, the configuration specified with `config` will be applied to subsequent commands in the pipeline.  Many command combinations are possible.

#### Examples
Composite two Landsat-7 images and download the result:
```shell
geedim composite -i LANDSAT/LE07/C02/T1_L2/LE07_173083_20100203 -i LANDSAT/LE07/C02/T1_L2/LE07_173083_20100219 download --bbox 22 -33.1 22.1 -33 --crs EPSG:3857 --scale 30
```
Composite the results of a Landsat-8 search and download the result.
```shell
geedim search -c landsat8_c2_l2 -s 2019-02-01 -e 2019-03-01 --bbox 23 -33 23.2 -33.2 composite -cm q-mosaic --mask download --scale 30 --crs EPSG:3857
```
Search for Sentinel-2 SR images with a cloudless portion of at least 60%, using the `qa` mask-method to identify clouds:
```shell
geedim config --mask-method qa search -c sentinel2_sr --cloudless-portion 60 -s 2022-01-01 -e 2022-01-14 --bbox 24 -34 24.5 -33.5
```


### API
#### Example

```python
import ee
from geedim import MaskedImage, MaskedCollection

ee.Initialize()  # initialise earth engine

# geojson region to search / download
region = {
    "type": "Polygon",
    "coordinates": [[[24, -33.6], [24, -33.53], [23.93, -33.53], [23.93, -33.6], [24, -33.6]]]
}

# make collection and search
gd_collection = MaskedCollection.from_name('COPERNICUS/S2_SR')
gd_collection = gd_collection.search('2019-01-10', '2019-01-21', region)
print(gd_collection.key_table)
print(gd_collection.properties_table)

# create and download an image
im = MaskedImage.from_id('COPERNICUS/S2_SR/20190115T080251_20190115T082230_T35HKC')
im.download('s2_image.tif', region=region)

# composite search results and download
comp_image = gd_collection.composite()
comp_image.download('s2_comp_image.tif', region=region, crs='EPSG:32735', scale=30)
```


## License
This project is licensed under the terms of the [Apache-2.0 License](LICENSE).

## Contributing
Contributions are welcome.  Report bugs or contact me with questions [here](https://github.com/dugalh/geedim/issues).

## Credits
- Tiled downloading was inspired by the work in [GEES2Downloader](https://github.com/cordmaur/GEES2Downloader) under terms of the [MIT license](https://github.com/cordmaur/GEES2Downloader/blob/main/LICENSE). 
- Medoid compositing was adapted from [gee_tools](https://github.com/gee-community/gee_tools) under the terms of the [MIT license](https://github.com/gee-community/gee_tools/blob/master/LICENSE).
- Sentinel-2 cloud/shadow masking was adapted from [ee_extra](https://github.com/r-earthengine/ee_extra) under terms of the [Apache-2.0 license](https://github.com/r-earthengine/ee_extra/blob/master/LICENSE)

## Author
**Dugal Harris** - [dugalh@gmail.com](mailto:dugalh@gmail.com)

