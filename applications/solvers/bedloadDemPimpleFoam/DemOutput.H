/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef DemOutput_H
#define DemOutput_H
namespace Foam
{
void DemCloud::openOutput()
{
    word name=cfg_.lookupOrDefault<word>("runName","bedload");
    requireDem(name!="."&&name!=".."&&name.find('/')==std::string::npos,"runName must be a simple name");
    output_=mesh_.time().globalPath()/"postProcessing/bedloadDEM"/(name+"-"+mesh_.time().timeName());
    requireDem(!exists(output_),"DEM output exists: "+output_+". Choose a new runName or a deliberate restart time");
    mkDir(output_);
    tracks_.open((output_/"tracks.csv").c_str());budget_.open((output_/"budget.csv").c_str());
    pvd_.open((output_/"particles.pvd").c_str());
    requireDem(tracks_.good()&&budget_.good()&&pvd_.good(),"Cannot create DEM outputs");
    tracks_<<std::setprecision(17)<<"time,id,x,y,z,ux,uy,uz,vx,vy,vz,omega_x,omega_y,omega_z,diameter,density,mobile\n";
    budget_<<std::setprecision(17)<<"time,particle_mass,mobile_mass,fluid_volume,fluid_px,fluid_py,fluid_pz,particle_px,particle_py,particle_pz,total_solid_flux_x,bedload_flux_x,moving_grains,continuity_l1,drag_impulse_x,drag_impulse_y,drag_impulse_z\n";
    pvd_<<"<?xml version=\"1.0\"?>\n<VTKFile type=\"Collection\" version=\"0.1\" byte_order=\"LittleEndian\"><Collection>\n";
    OFstream settings(output_/"demCouplingProperties");settings.precision(17);cfg_.writeHeader(settings);cfg_.dictionary::write(settings,false);
    Info<<"DEM outputs: "<<output_<<nl;
}
void DemCloud::write(scalar continuity)
{
    const auto& eps=mesh_.lookupObject<volScalarField>("porosity");
    const auto& U=mesh_.lookupObject<volVectorField>("U");
    scalar fluidVolume=fvc::domainIntegrate(eps).value();
    vector fluidMomentum=rho_*fvc::domainIntegrate(eps*U).value();
    if(!Pstream::master())return;
    scalar t=mesh_.time().value(),total=0,mobile=0,flux=0,bedFlux=0;
    scalar elevation=cfg_.lookupOrDefault<scalar>("bedloadElevation",box_.max().z());
    scalar movingSpeed=demPositive(cfg_,"movingSpeed",.001);
    vector momentum=Zero;label moving=0;
    forAll(position,i)
    {
        scalar volume=4*constant::mathematical::pi*pow3(radius[i])/3;
        total+=mass[i];
        if(type[i]==1)
        {
            mobile+=mass[i];momentum+=mass[i]*velocity[i];flux+=volume*velocity[i].x();
            if(position[i].z()<=elevation){bedFlux+=volume*velocity[i].x();moving+=mag(velocity[i])>=movingSpeed;}
        }
        tracks_<<t<<','<<i+1<<','<<position[i].x()<<','<<position[i].y()<<','<<position[i].z()<<','
            <<unwrapped[i].x()<<','<<unwrapped[i].y()<<','<<unwrapped[i].z()<<','
            <<velocity[i].x()<<','<<velocity[i].y()<<','<<velocity[i].z()<<','
            <<omega[i].x()<<','<<omega[i].y()<<','<<omega[i].z()<<','<<2*radius[i]<<','<<mass[i]/volume<<','<<(type[i]==1)<<'\n';
    }
    scalar area=box_.span().x()*box_.span().y();
    budget_<<t<<','<<total<<','<<mobile<<','<<fluidVolume<<','<<fluidMomentum.x()<<','<<fluidMomentum.y()<<','<<fluidMomentum.z()<<','
        <<momentum.x()<<','<<momentum.y()<<','<<momentum.z()<<','<<flux/area<<','<<bedFlux/area<<','<<moving<<','<<continuity<<','
        <<lastImpulse_.x()<<','<<lastImpulse_.y()<<','<<lastImpulse_.z()<<'\n';
    fileName name="particles_"+Foam::name(frame_++)+".vtk";std::ofstream vtk((output_/name).c_str());
    vtk<<std::setprecision(17)<<"# vtk DataFile Version 3.0\nLAMMPS physical grains\nASCII\nDATASET POLYDATA\nPOINTS "<<position.size()<<" double\n";
    for(const point& x:position)vtk<<x.x()<<' '<<x.y()<<' '<<x.z()<<'\n';
    vtk<<"VERTICES "<<position.size()<<' '<<2*position.size()<<'\n';
    forAll(position,i)vtk<<"1 "<<i<<'\n';
    vtk<<"POINT_DATA "<<position.size()<<"\nVECTORS velocity double\n";
    for(const vector& v:velocity)vtk<<v.x()<<' '<<v.y()<<' '<<v.z()<<'\n';
    for(const word& field:{word("id"),word("diameter"),word("mobile")})
    {
        vtk<<"SCALARS "<<field<<" double 1\nLOOKUP_TABLE default\n";
        forAll(position,i)vtk<<(field=="id"?i+1:field=="diameter"?2*radius[i]:type[i]==1)<<'\n';
    }
    pvd_<<std::setprecision(17)<<"<DataSet timestep=\""<<t<<"\" group=\"\" part=\"0\" file=\""<<name<<"\"/>\n";
    tracks_.flush();budget_.flush();pvd_.flush();vtk.close();
    requireDem(tracks_.good()&&budget_.good()&&pvd_.good()&&vtk.good(),"Failed DEM output write");
    fileName checkpoint=mesh_.time().globalPath()/mesh_.time().timeName()/"uniform/DEM";mkDir(checkpoint);
    command("write_restart "+quotePath(checkpoint/"lammps.restart"));
    dictionary state;state.add("schema",label(1));state.add("time",t);state.add("contact",contact_);
    OFstream metadata(checkpoint/"couplingState");metadata.precision(17);state.write(metadata,false);
    requireDem(metadata.good(),"Cannot finish DEM coupling checkpoint");
}
}
#endif
