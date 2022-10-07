# [initialise-start]
import geedim as gd
gd.Initialize()
# [initialise-end]

# [search-start]
# geojson search polygon
region = {
    'type': 'Polygon', 'coordinates': [[
        (19, -34), (19, -33.8), (18.8, -33.8), (18.8, -34), (19., -34)
    ]]
}

# create and search a landsat-8 collection
coll = gd.MaskedCollection.from_name('LANDSAT/LC08/C02/T1_L2')
filt_coll = coll.search('2019-01-01', '2019-02-15', region)

# display the search results
print(filt_coll.schema_table)
print(filt_coll.properties_table)
# [search-end]

# [image-download-start]
# create a landsat-8 image from its ID
im = gd.MaskedImage.from_id(
    'LANDSAT/LC08/C02/T1_L2/LC08_175083_20190117', mask=False)

# download a region of the image with 'average' resampling to 60m pixels, and
# data type conversion to 16 bit unsigned int
im.download('landsat_8_image.tif', region=region, resampling='average',
            scale=60, dtype='uint16')
# [image-download-end]

# [composite-start]
# find a 'q-mosaic' composite image of the search result images, prioritising
# the least cloudy image by specifying `region`
comp_im = filt_coll.composite(method='q-mosaic', region=region)
# download the composite, specifying crs, region, and scale
comp_im.download('landsat_8_comp_image.tif', region=region, crs='EPSG:32634',
                 scale=30)
# [composite-end]

# [mask-start]
# create a cloud/shadow masked Sentinel-2 image, specifying a cloud
# probability threshold of 30%
im = gd.MaskedImage.from_id(
    'COPERNICUS/S2_SR/20190101T082331_20190101T084846_T34HCH', mask=True, prob=30)
# download a region of the masked image, downsampling to 20m pixels
im.download('s2_sr_image.tif', region=region, scale=20, resampling='average')
# [mask-end]

# [metadata-start]
import rasterio as rio
with rio.open('s2_sr_image.tif', 'r') as ds:
    print('Image properties:\n', ds.tags())
    print('Band names:\n', ds.descriptions)
    print('Band 1 properties:\n', ds.tags(1))
# [metadata-end]
