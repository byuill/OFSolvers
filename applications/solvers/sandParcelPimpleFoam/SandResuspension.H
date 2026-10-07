/* SPDX-License-Identifier: GPL-3.0-or-later
 * Optional empirical entrainment of deposited native parcels; no new mass.
 * Bed geometry is fixed. The threshold permits release, while an integrated
 * hazard determines when a finite parcel actually leaves its resting state.
 */
#ifndef SandResuspension_H
#define SandResuspension_H
#include <cmath>
#include <cstdint>
#include "SandKinematicCloud.H"
#include "turbulentTransportModel.H"
#include "wallPolyPatch.H"
#include "HashSet.H"
namespace Foam
{
class SandResuspension
{
    SandKinematicCloud& cloud_;
    const incompressible::turbulenceModel& turbulence_;
    bool enabled_,writeEvents_;
    word thresholdModel_;
    labelList patches_;
    scalar critical_,rate_,exponent_,restTime_,launchSpeed_,launchFactor_,liftDiameters_,liftHeight_;
    label seed_;

    bool selected(label patch) const
    { for(label p:patches_) if(p==patch) return true; return false; }
    // One exponential waiting threshold per deposition episode. Cumulative
    // integrated hazard, rather than a fresh Bernoulli draw per step, gives
    // the same release time when a constant-rate interval is subdivided.
    scalar threshold(const basicKinematicParcel& p,label episode) const
    {
        std::uint64_t x=std::uint64_t(p.origId())
            ^(std::uint64_t(p.origProc())*UINT64_C(0x9e3779b97f4a7c15))
            ^(std::uint64_t(episode)*UINT64_C(0xbf58476d1ce4e5b9))
            ^(std::uint64_t(seed_)*UINT64_C(0x94d049bb133111eb));
        x+=UINT64_C(0x9e3779b97f4a7c15);
        x=(x^(x>>30))*UINT64_C(0xbf58476d1ce4e5b9);
        x=(x^(x>>27))*UINT64_C(0x94d049bb133111eb);
        x^=x>>31;
        scalar u=(scalar(x>>12)+0.5)/scalar(UINT64_C(4503599627370496));
        return -std::log(u);
    }
    void contact(basicKinematicParcel& p,scalar time)
    {
        auto& records=cloud_.bedHistory().records();
        word id=SandBedHistory::key(p);
        label episode=records.found(id) ? records.subDict(id).get<label>("episode")+1 : 1;
        dictionary r;
        r.add("resting",true);
        r.add("patch",cloud_.mesh().boundaryMesh()[p.patch()].name());
        r.add("face",cloud_.mesh().boundaryMesh()[p.patch()].whichFace(p.face()));
        r.add("depositedTime",time);
        r.add("episode",episode);
        r.add("hazard",scalar(0));
        r.add("threshold",threshold(p,episode));
        records.set(id,r);
    }
    void restoreContactFace(basicKinematicParcel& p) const
    {
        // Native cloud position files preserve tetrahedral addressing but may
        // omit the instantaneous face flag. Restore that flag only for an
        // inactive parcel whose saved tet lies exactly on a selected bed face.
        const auto& mesh=cloud_.mesh();
        if(!p.active() && !p.onBoundaryFace() && p.tetFace()>=mesh.nInternalFaces())
        {
            label patch=mesh.boundaryMesh().whichPatch(p.tetFace());
            if(patch>=0 && selected(patch) && mag(p.coordinates()[0])<1e-8)
                p.face()=p.tetFace();
        }
    }
    void validateRest(const basicKinematicParcel& p,const dictionary& r) const
    {
        const auto& mesh=cloud_.mesh();
        label patch=mesh.boundaryMesh().findPatchID(r.getWord("patch"));
        label face=r.get<label>("face");
        if(p.active() || patch<0 || !selected(patch) || !p.onBoundaryFace()
         || p.patch()!=patch || face<0 || face>=mesh.boundary()[patch].size()
         || p.face()!=mesh.boundaryMesh()[patch].start()+face)
            FatalErrorInFunction << "Resting parcel and saved bed face disagree: "
                << SandBedHistory::key(p) << exit(FatalError);
        scalar time=r.get<scalar>("depositedTime"),hazard=r.get<scalar>("hazard"),limit=r.get<scalar>("threshold");
        if(!(std::isfinite(time) && std::isfinite(hazard) && std::isfinite(limit)
           && time<=mesh.time().value()+1e-10*max(scalar(1),mag(mesh.time().value()))
           && hazard>=0 && limit>hazard
           && r.get<label>("episode")>=1))
            FatalErrorInFunction << "Invalid bed residence/release history" << exit(FatalError);
    }
public:
    SandResuspension(SandKinematicCloud& cloud,const incompressible::turbulenceModel& turbulence)
    :cloud_(cloud),turbulence_(turbulence),enabled_(false),writeEvents_(false),
     critical_(0),rate_(0),exponent_(0),restTime_(0),launchSpeed_(0),launchFactor_(0),
     liftDiameters_(0),liftHeight_(0),seed_(0)
    {
        const dictionary dict=cloud.particleProperties().subOrEmptyDict("resuspension");
        enabled_=dict.lookupOrDefault<bool>("enabled",false);
        if(!enabled_) return; // No history I/O, randomness or altered tracking.
        thresholdModel_=dict.lookupOrDefault<word>("thresholdModel","Shields");
        if(thresholdModel_=="Shields") critical_=dict.lookupOrDefault<scalar>("criticalShields",0.05);
        else if(thresholdModel_=="shearStress") critical_=dict.get<scalar>("criticalShearStress");
        else if(thresholdModel_=="nearBedVelocity") critical_=dict.get<scalar>("criticalVelocity");
        else FatalErrorInFunction << "thresholdModel must be Shields, shearStress or nearBedVelocity" << exit(FatalError);
        rate_=dict.lookupOrDefault<scalar>("releaseRate",1); // s^-1
        exponent_=dict.lookupOrDefault<scalar>("releaseExponent",1.5);
        restTime_=dict.lookupOrDefault<scalar>("minimumRestTime",0.1); // seconds
        launchSpeed_=dict.lookupOrDefault<scalar>("normalLaunchSpeed",0.02); // m/s
        launchFactor_=dict.lookupOrDefault<scalar>("launchShearFactor",0); // dimensionless
        liftDiameters_=dict.lookupOrDefault<scalar>("liftDiameters",2);
        liftHeight_=dict.lookupOrDefault<scalar>("liftHeight",0); // 0 -> use grain diameters
        seed_=dict.lookupOrDefault<label>("seed",67890);
        writeEvents_=dict.lookupOrDefault<bool>("writeEvents",false);
        const scalar inputs[]={critical_,rate_,exponent_,restTime_,launchSpeed_,launchFactor_,liftDiameters_,liftHeight_};
        for(scalar value:inputs) if(!std::isfinite(value))
            FatalErrorInFunction << "Resuspension inputs must be finite" << exit(FatalError);
        if(!(critical_>0 && rate_>=0 && exponent_>0 && restTime_>=0
           && launchSpeed_>=0 && launchFactor_>=0 && liftDiameters_>0 && liftHeight_>=0))
            FatalErrorInFunction << "Invalid resuspension threshold, release or launch settings" << exit(FatalError);
        wordList names(dict.lookup("bedPatches"));
        if(names.empty()) FatalErrorInFunction << "Enabled resuspension needs bedPatches" << exit(FatalError);
        patches_.setSize(names.size());
        forAll(names,i)
        {
            label patch=cloud.mesh().boundaryMesh().findPatchID(names[i]);
            if(patch<0 || !isA<wallPolyPatch>(cloud.mesh().boundaryMesh()[patch]))
                FatalErrorInFunction << "Resuspension bed must be a wall: " << names[i] << exit(FatalError);
            for(label j=0;j<i;++j) if(patches_[j]==patch)
                FatalErrorInFunction << "Duplicate resuspension bed patch" << exit(FatalError);
            patches_[i]=patch;
        }
        cloud.enableBedHistory(dict.lookupOrDefault<bool>("initializeHistoryOnRestart",false));
        auto& records=cloud.bedHistory().records();
        HashSet<word> present;
        for(auto& p:cloud)
        {
            restoreContactFace(p);
            word id=SandBedHistory::key(p); present.insert(id);
            if(records.found(id))
            {
                const auto& r=records.subDict(id);
                if(r.get<bool>("resting")) validateRest(p,r);
                else if(!p.active())
                    FatalErrorInFunction << "Inactive parcel has mobile restart history" << exit(FatalError);
            }
            else if(!p.active() && p.onBoundaryFace() && selected(p.patch()))
            {
                if(cloud.bedHistory().loaded())
                    FatalErrorInFunction << "Missing resting parcel history " << id << exit(FatalError);
                contact(p,cloud.mesh().time().value());
            }
        }
        for(const word& id:records.toc()) if(!present.found(id))
            FatalErrorInFunction << "Saved history has no matching parcel " << id << exit(FatalError);
        Info<< "Sand resuspension enabled: " << thresholdModel_ << " critical="
            << critical_ << " releaseRate=" << rate_ << " /s" << endl;
    }
    bool enabled() const { return enabled_; }
    void beforeEvolve()
    {
        if(!enabled_) return;
        const auto& mesh=cloud_.mesh();
        scalar end=mesh.time().value(),dt=mesh.time().deltaTValue(),start=end-dt;
        const volSymmTensorField stress(turbulence_.devReff());
        auto& records=cloud_.bedHistory().records();
        label events=0;scalar released=0,maxRatio=0;
        for(auto& p:cloud_)
        {
            word id=SandBedHistory::key(p);
            if(p.active() || !records.found(id)) continue;
            auto& r=records.subDict(id);
            if(!r.get<bool>("resting")) continue;
            validateRest(p,r);
            label patch=p.patch(),face=r.get<label>("face"),cell=mesh.boundary()[patch].faceCells()[face];
            vector n=mesh.Sf().boundaryField()[patch][face]/mesh.magSf().boundaryField()[patch][face];
            vector traction=n & stress.boundaryField()[patch][face];
            scalar kinematicShear=mag(traction-n*(n & traction));
            scalar rhoFluid=cloud_.rho()[cell];
            if(!(rhoFluid>0 && p.rho()>rhoFluid && p.d()>0 && std::isfinite(kinematicShear)))
                FatalErrorInFunction << "Invalid density, size or bed stress for resuspension" << exit(FatalError);
            scalar shearStress=rhoFluid*kinematicShear;
            vector carrier=cloud_.U()[cell];
            vector tangential=carrier-n*(carrier & n);
            scalar indicator=thresholdModel_=="Shields"
                ? shearStress/((p.rho()-rhoFluid)*mag(cloud_.g().value())*p.d())
                : (thresholdModel_=="shearStress" ? shearStress : mag(tangential));
            scalar ratio=indicator/critical_;
            maxRatio=max(maxRatio,ratio);
            scalar eligibleStart=max(start,r.get<scalar>("depositedTime")+restTime_);
            scalar eligibleDt=max(scalar(0),end-eligibleStart);
            if(ratio<=1 || rate_==0 || eligibleDt==0) continue;
            scalar lambda=rate_*Foam::pow(ratio-1,exponent_);
            if(!std::isfinite(lambda))
                FatalErrorInFunction << "Nonfinite release rate; adjust resuspension inputs" << exit(FatalError);
            scalar hazard=r.get<scalar>("hazard"),limit=r.get<scalar>("threshold");
            scalar waiting=(limit-hazard)/lambda;
            if(waiting>eligibleDt)
            {
                r.set("hazard",hazard+lambda*eligibleDt);
                continue;
            }
            scalar releaseTime=eligibleStart+waiting;
            scalar fraction=min(scalar(1),max(scalar(0),(releaseTime-start)/dt));
            scalar lift=liftHeight_>0 ? liftHeight_ : liftDiameters_*p.d();
            scalar cellDepth=mag((mesh.C()[cell]-mesh.Cf().boundaryField()[patch][face]) & n);
            if(!(lift>0 && lift<0.5*cellDepth))
                FatalErrorInFunction << "Lift distance must be positive and less than half "
                    << "the bed-face-to-cell-centre normal distance" << exit(FatalError);
            scalar mass=p.nParticle()*p.mass();
            vector position=p.position()-lift*n;
            p.relocate(position,cell); // rebuild barycentric position AND topology
            if(p.cell()!=cell || p.onBoundaryFace())
                FatalErrorInFunction << "Resuspension relocation left the bed owner cell" << exit(FatalError);
            p.active(true);
            p.U()=tangential-(launchSpeed_+launchFactor_*Foam::sqrt(kinematicShear))*n;
            p.UTurb()=Zero;p.tTurb()=GREAT; // request a fresh native random eddy
            cloud_.scheduleRelease(p,fraction); // advance only remaining release time
            r.set("resting",false);
            r.set("hazard",limit);
            cloud_.bedHistory().addRelease(mass);
            ++events;released+=mass;
            if(writeEvents_) Pout<< "Sand release: id=" << id << " episode=" << r.get<label>("episode")
                << " time=" << releaseTime << " ratio=" << ratio << " mass=" << mass << endl;
        }
        reduce(events,sumOp<label>());reduce(released,sumOp<scalar>());reduce(maxRatio,maxOp<scalar>());
        scalar cumulative=returnReduce(cloud_.bedHistory().releasedMass(),sumOp<scalar>());
        Info<< "Sand resuspension: released=" << events << " mass=" << released
            << " cumulative=" << cumulative << " kg; maxThresholdRatio=" << maxRatio << endl;
    }
    void afterEvolve()
    {
        if(!enabled_) return;
        auto& records=cloud_.bedHistory().records();HashSet<word> present;
        for(auto& p:cloud_)
        {
            word id=SandBedHistory::key(p);present.insert(id);
            if(!p.active() && p.onBoundaryFace() && selected(p.patch()))
            {
                if(!records.found(id) || !records.subDict(id).get<bool>("resting"))
                    contact(p,cloud_.mesh().time().value());
                else validateRest(p,records.subDict(id));
            }
        }
        // Escaped particles have no bed inventory/history left on this rank.
        for(const word& id:records.toc()) if(!present.found(id)) records.remove(id);
    }
    void write()
    { if(enabled_ && cloud_.mesh().time().writeTime()) cloud_.bedHistory().write(cloud_.mesh()); }
};
}
#endif
