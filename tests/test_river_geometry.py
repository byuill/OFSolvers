import numpy as np
import pyvista as pv
import pytest
from mesh_config import block_mesh_dictionary, retained_point_valid, find_retained_point
from terrain_geometry import dem_solid


def test_retained_point_rejects_outside_bounds_and_wrong_surface_region():
    cube=pv.Box(bounds=(-1,1,-1,1,-1,1)).triangulate()
    with pytest.raises(ValueError):retained_point_valid((10,0,0),(-3,3,-3,3,-3,3),cube,False)
    with pytest.raises(ValueError):retained_point_valid((0,0,0),(-3,3,-3,3,-3,3),cube,False)
    left=pv.Box(bounds=(-2,-1,-1,1,-1,1));right=pv.Box(bounds=(1,2,-1,1,-1,1))
    disconnected=left.merge(right).triangulate()
    assert retained_point_valid((0,0,0),(-3,3,-3,3,-3,3),disconnected,False)
    inside=find_retained_point((-3,3,-3,3,-3,3),disconnected,True)
    assert abs(inside[0])>=1 and retained_point_valid(inside,(-3,3,-3,3,-3,3),disconnected,True)
    with pytest.raises(ValueError):find_retained_point((-3,3,-3,3,-3,3),pv.Plane(),True)


def test_background_mesh_rejects_invalid_ranges_and_counts():
    with pytest.raises(ValueError):block_mesh_dictionary((1,0,0,1,0,1),(2,2,2),(1,1,1))
    with pytest.raises(ValueError):block_mesh_dictionary((0,1,0,1,0,1),(0,2,2),(1,1,1))
    text=block_mesh_dictionary((0,1,0,2,0,3),(2,3,4),(1,1,1),{'zMin':'wall'})
    assert 'zMin { type wall;' in text and 'xMin { type patch;' in text


def write_raster(path,data,transform,crs='EPSG:32615',nodata=None):
    rasterio=pytest.importorskip('rasterio')
    with rasterio.open(path,'w',driver='GTiff',height=data.shape[0],width=data.shape[1],
                       count=1,dtype='float32',crs=crs,transform=transform,nodata=nodata) as dst:
        dst.write(data.astype('float32'),1)


def test_dem_uses_rotated_affine_pixel_centres(tmp_path):
    from affine import Affine
    transform=Affine(2,.5,100,.25,-3,200)
    path=tmp_path/'terrain.tif';data=np.arange(12).reshape(3,4)
    write_raster(path,data,transform)
    solid=dem_solid(path,normalize=False,depth=5)
    columns,rows=np.meshgrid(np.arange(4)+.5,np.arange(3)+.5)
    xx=2*columns+.5*rows+100;yy=.25*columns-3*rows+200
    assert np.allclose(solid.bounds[:4],(xx.min(),xx.max(),yy.min(),yy.max()))
    assert solid.is_manifold and solid.volume>0 and np.isfinite(solid.points).all()
    assert solid.bounds[4]==-5 and solid.bounds[5]==11


def test_dem_invalid_masks_geographic_crs_and_downsample(tmp_path):
    from affine import Affine
    path=tmp_path/'terrain.tif';transform=Affine(2,0,100,0,-2,200)
    data=np.ones((3,3));data[1,1]=-9999
    write_raster(path,data,transform,nodata=-9999)
    with pytest.raises(ValueError,match='NoData'):dem_solid(path)
    assert dem_solid(path,fill_missing=True).is_manifold
    with pytest.raises(ValueError,match='two rows'):dem_solid(path,factor=100)
    write_raster(path,np.full((3,3),-9999),transform,nodata=-9999)
    with pytest.raises(ValueError,match='no valid'):dem_solid(path)
    write_raster(path,np.ones((3,3)),transform,crs='EPSG:4326')
    with pytest.raises(ValueError,match='projected'):dem_solid(path)
