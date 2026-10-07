"""DEM conversion respecting affine pixel centres, projected units and masks."""
import math
import numpy as np
import pyvista as pv


def dem_solid(path, factor=1, z_scale=1, depth=10, normalize=True, fill_missing=False):
    import rasterio
    from affine import Affine
    if factor < 1 or not math.isfinite(z_scale) or z_scale <= 0 or not math.isfinite(depth) or depth <= 0:
        raise ValueError('Downsampling, Z scale and extrusion depth must be positive.')
    with rasterio.open(path) as src:
        if src.crs is None or not src.crs.is_projected:
            raise ValueError('DEM needs a projected CRS. Reproject geographic degrees into a suitable local metre-based CRS first.')
        ny, nx = math.ceil(src.height/factor), math.ceil(src.width/factor)
        if min(nx, ny) < 2:
            raise ValueError('Downsampling must retain at least two rows and two columns.')
        data = src.read(1, out_shape=(ny, nx), masked=True).astype(float)
        valid = ~np.ma.getmaskarray(data) & np.isfinite(data.data)
        if not valid.any():
            raise ValueError('DEM contains no valid elevations.')
        z = np.array(data.data)
        if not valid.all():
            if not fill_missing:
                raise ValueError('DEM contains NoData/invalid elevations. Crop it or explicitly enable the missing-cell fill option.')
            z[~valid] = z[valid].min()
        scale = Affine.scale(src.width/nx, src.height/ny)
        transform = src.transform @ scale if hasattr(Affine,'__matmul__') else src.transform*scale
        columns, rows = np.meshgrid(np.arange(nx)+0.5, np.arange(ny)+0.5)
        xx = (transform.a*columns + transform.b*rows + transform.c)*src.crs.linear_units_factor[1]
        yy = (transform.d*columns + transform.e*rows + transform.f)*src.crs.linear_units_factor[1]
    if normalize:
        xx -= xx.min(); yy -= yy.min(); z -= z.min()
    z *= z_scale
    bottom = np.full_like(z, z.min()-depth)
    grid = pv.StructuredGrid(np.stack((xx,xx),axis=-1), np.stack((yy,yy),axis=-1),
                             np.stack((bottom,z),axis=-1))
    solid = grid.extract_surface(algorithm='dataset_surface').triangulate().clean()
    solid = solid.compute_normals(consistent_normals=True, auto_orient_normals=True)
    if not solid.is_manifold or not np.isfinite(solid.points).all():
        raise ValueError('Generated terrain surface is not a finite closed manifold.')
    return solid
