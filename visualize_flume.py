"""Plot saved OpenFOAM velocity on the central longitudinal x-z transect."""
import argparse
import json
from pathlib import Path

import numpy as np
import pyvista as pv

from castle_flume import castle_profile


def plot_velocity(case, output, time=None, nx=751, nz=251):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon
    from matplotlib.tri import Triangulation
    case, output = Path(case).resolve(), Path(output).resolve()
    settings = json.loads((case/'flume.json').read_text())['settings']
    reader = pv.OpenFOAMReader(case/'case.foam')
    reader.disable_all_cell_arrays()
    reader.enable_cell_array('U')
    has_alpha = 'alpha.water' in reader.cell_array_names
    if has_alpha:
        reader.enable_cell_array('alpha.water')
    reader.disable_all_patch_arrays()
    reader.enable_patch_array('internalMesh')
    reader.cell_to_point_creation = False
    saved = max(reader.time_values) if time is None else time
    reader.set_active_time_value(saved)
    mesh = reader.read()['internalMesh']
    xx = np.linspace(.001,2.999,nx)
    zz = np.linspace(.001,.999,nz)
    x,z = np.meshgrid(xx,zz)
    probes = pv.PolyData(np.column_stack((x.ravel(),np.full(x.size,.125),z.ravel())))
    sampled = probes.sample(mesh)
    valid = sampled['vtkValidPointMask'].astype(bool).reshape(x.shape)
    u = np.asarray(sampled['U']).reshape(nz,nx,3)
    alpha = np.asarray(sampled['alpha.water']).reshape(x.shape) if has_alpha else np.ones(x.shape)
    wet = valid & (alpha >= .5)
    cut = mesh.slice(normal=(0,1,0),origin=(1.5,.125,.5)).triangulate().clean()
    triangles = cut.faces.reshape(-1,4)[:,1:]
    vertices = cut.points[:,[0,2]]
    edge1 = vertices[triangles[:,1]]-vertices[triangles[:,0]]
    edge2 = vertices[triangles[:,2]]-vertices[triangles[:,0]]
    area = np.abs(edge1[:,0]*edge2[:,1]-edge1[:,1]*edge2[:,0])
    cut_alpha = np.asarray(cut.cell_data['alpha.water']) if has_alpha else np.ones(cut.n_cells)
    cut_speed = np.linalg.norm(cut.cell_data['U'],axis=1)
    triangulation = Triangulation(vertices[:,0],vertices[:,1],triangles)
    triangulation.set_mask((cut_alpha < .5) | (area < 1e-14))
    vmax = max(float(cut_speed[cut_alpha >= .5].max()),1e-6)
    fig,ax = plt.subplots(figsize=(13,5.5),layout='constrained')
    ax.set_facecolor('#e9eff5')
    image = ax.tripcolor(triangulation,facecolors=cut_speed,shading='flat',cmap='viridis',vmin=0,vmax=vmax,rasterized=True)
    if has_alpha:
        phase_tri = Triangulation(vertices[:,0],vertices[:,1],triangles)
        phase_tri.set_mask(area < 1e-14)
        phase_points = cut.cell_data_to_point_data()['alpha.water']
        ax.tricontour(phase_tri,phase_points,levels=[.5],colors=['#16b9c8'],linewidths=1.4)
    ax.add_patch(Polygon(castle_profile(),closed=True,facecolor='#596473',edgecolor='#2d3540',linewidth=1,zorder=5))
    rows,cols = slice(6,None,16),slice(10,None,28)
    ax.quiver(x[rows,cols],z[rows,cols],np.ma.array(u[:,:,0],mask=~wet)[rows,cols],
              np.ma.array(u[:,:,2],mask=~wet)[rows,cols],color='white',angles='xy',
              scale_units='xy',scale=12*vmax,width=.0014,pivot='mid',zorder=4)
    ax.set(xlim=(0,3),ylim=(0,1),xlabel='Downstream distance x (m)',ylabel='Elevation z (m)',aspect='equal')
    ax.set_title(f'Central longitudinal transect: y = 0.125 m  |  t = {saved:g} s\n'
                 f'Water depth {settings["depth"]:g} m · inlet {settings["velocity"]:g} m/s · STARTUP snapshot',loc='left',fontsize=12,pad=12)
    fig.colorbar(image,ax=ax,label='Water speed |U| (m/s)',fraction=.026,pad=.025)
    ax.text(1.5,.83,'CASTLE (solid)',ha='center',fontsize=10,color='#3c4653')
    if has_alpha:
        ax.text(.12,.84,'AIR (masked)',fontsize=10,color='#586b7c')
    phase_caption = 'Cyan: water fraction α = 0.5. ' if has_alpha else 'Filled flume. '
    ax.text(.02,-.22,'White arrows: projected velocity (Ux, Uz). '+phase_caption+
            'Central plane intersects the castle; side-gap jets are outside this plane.\n'
            'Early transient only: this is not a developed wake or a converged hydraulic prediction.',
            transform=ax.transAxes,fontsize=9,color='#4b5563',va='top')
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=180,bbox_inches='tight')
    svg = output.with_suffix('.svg')
    fig.savefig(svg,bbox_inches='tight')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(fig)
    np.savez_compressed(output.with_suffix('.npz'),x=x,z=z,U=u,alpha=alpha,valid=valid,
                        time=saved,transect_y=.125,plane_points=vertices,plane_triangles=triangles,
                        plane_U=np.asarray(cut.cell_data['U']),plane_alpha=cut_alpha)
    print(f'{output}: t={saved:g}, peak central-plane water speed={vmax:.6g} m/s')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--time',type=float)
    args = parser.parse_args()
    plot_velocity(args.case,args.output,args.time)
