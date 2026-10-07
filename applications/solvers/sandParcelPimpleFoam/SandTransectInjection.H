/* SPDX-License-Identifier: GPL-3.0-or-later
 * New v1912 injection submodel. Native InjectionModel supplies timing, parcel
 * weights, mass accounting and restart state. Here we only specify geometry,
 * the feed amount and the probability distribution of injection positions.
 */
#ifndef SandTransectInjection_H
#define SandTransectInjection_H
#include <cmath>
#include <cstdint>
#include "InjectionModel.H"
#include "turbulentTransportModel.H"
#include "wallPolyPatch.H"
#include "triangle.H"
#include "Pstream.H"

namespace Foam
{
template<class CloudType>
class SandTransectInjection : public InjectionModel<CloudType>
{
    // Each clipped triangle belongs to one real inlet face on this rank.
    struct Piece
    {
        vector a,b,c; // coordinates (horizontal width, height, 0) in inlet plane
        label face;
        scalar area;
    };
    word patchName_, bedPatchName_, profile_, shearModel_, velocityModel_;
    label patchID_, bins_, seed_;
    vector origin_, widthDir_, up_, inward_, selectedU_;
    scalar width_, depth_, a_, zMax_, offset_, duration_, feedRate_, parcelRate_;
    scalar d_, kappa_, beta_, shearLength_, prescribedShear_, ws_, uStar_, exponent_;
    bool fluxWeighted_, writeSamples_;
    DynamicList<Piece> pieces_;
    scalarField cumulative_, procTotals_;

    // InjectionModel's native interface takes total mass. Translate our
    // constant kg/s feed rate into that interface without exposing redundant
    // user inputs that could disagree. Fractional parcel weights are allowed.
    static dictionary nativeDictionary(const dictionary& input)
    {
        dictionary d(input);
        scalar rate=readScalar(d.lookup("massFeedRate"));
        scalar duration=readScalar(d.lookup("duration"));
        d.set("massTotal", rate*duration);
        d.set("parcelBasisType", word("mass"));
        d.set("minParticlesPerParcel", scalar(0));
        if (!d.found("SOI")) d.add("SOI", scalar(0));
        return d;
    }

    // Sutherland-Hodgman clipping: restrict an inlet polygon to one side of
    // a line in its 2-D coordinate plane. This makes width enforcement exact,
    // including faces intersecting the edges of a narrow feed strip.
    static DynamicList<vector> clip
    (
        const UList<vector>& polygon, label axis, scalar bound, bool keepAbove
    )
    {
        DynamicList<vector> out;
        if (polygon.empty()) return out;
        vector previous=polygon.last();
        bool prevInside=keepAbove ? previous[axis]>=bound : previous[axis]<=bound;
        for (const vector& current : polygon)
        {
            bool inside=keepAbove ? current[axis]>=bound : current[axis]<=bound;
            if (inside != prevInside)
            {
                scalar t=(bound-previous[axis])/(current[axis]-previous[axis]);
                out.append(previous+t*(current-previous));
            }
            if (inside) out.append(current);
            previous=current;
            prevInside=inside;
        }
        return out;
    }

    // Counter-based SplitMix64: each global parcel index has its own random
    // coordinates. Injection sampling is reproducible after restart and does
    // not depend on unrelated draws by the native cloud/dispersion RNG.
    scalar random01(label parcel, unsigned lane) const
    {
        std::uint64_t x=std::uint64_t(parcel)+std::uint64_t(seed_)
            +std::uint64_t(lane)*UINT64_C(0x9e3779b97f4a7c15);
        x+=UINT64_C(0x9e3779b97f4a7c15);
        x=(x^(x>>30))*UINT64_C(0xbf58476d1ce4e5b9);
        x=(x^(x>>27))*UINT64_C(0x94d049bb133111eb);
        x=x^(x>>31);
        // Never return 0 or 1: do not inject on a triangle edge/corner.
        return (scalar(x>>12)+scalar(0.5))/scalar(UINT64_C(4503599627370496));
    }

    void geometry()
    {
        const auto& mesh=this->owner().mesh();
        const auto& patch=mesh.boundaryMesh()[patchID_];
        vector normalSum=gSum(mesh.Sf().boundaryField()[patchID_]);
        if (!(mag(normalSum)>SMALL))
            FatalErrorInFunction << "Empty or invalid feed patch" << exit(FatalError);
        inward_=-normalSum/mag(normalSum);
        if (mag(inward_ & up_)>1e-6 || mag(inward_ & widthDir_)>1e-6)
            FatalErrorInFunction << "Transect needs a planar vertical inlet; widthDirection "
                << "must lie in that plane" << exit(FatalError);
        pieces_.clear();
        scalar areaTotal=0;
        // Face-centre fan triangulation follows the native planar face geometry.
        forAll(patch, facei)
        {
            const face& face=patch[facei];
            const vector fc=mesh.Cf().boundaryField()[patchID_][facei];
            if (mag((fc-origin_) & inward_)>1e-7*depth_)
                FatalErrorInFunction << "origin must lie in the planar inlet" << exit(FatalError);
            vector normal=mesh.Sf().boundaryField()[patchID_][facei]
                /mesh.magSf().boundaryField()[patchID_][facei];
            if (mag(normal+inward_)>1e-6)
                FatalErrorInFunction << "Nonplanar inlet is unsupported" << exit(FatalError);
            scalar firstCellDepth=mag((mesh.C()[mesh.boundary()[patchID_].faceCells()[facei]]-fc) & inward_);
            if (!(offset_<0.1*firstCellDepth))
                FatalErrorInFunction << "inwardOffset must be small compared with the first inlet cell" << exit(FatalError);
            forAll(face, edge)
            {
                const vector vertices[3]={fc, mesh.points()[face[edge]],
                    mesh.points()[face[(edge+1)%face.size()]]};
                DynamicList<vector> triangle;
                for (const vector& point : vertices)
                {
                    if (mag((point-origin_) & inward_)>1e-7*depth_)
                        FatalErrorInFunction << "Nonplanar inlet vertices" << exit(FatalError);
                    vector r=point-origin_;
                    triangle.append(vector(r & widthDir_, r & up_, 0));
                }
                auto strip=clip(triangle,0,-width_/2,true);
                strip=clip(strip,0,width_/2,false);
                if (strip.empty()) continue;
                scalar low=GREAT, high=-GREAT;
                for (const vector& point:strip) { low=min(low,point.y()); high=max(high,point.y()); }
                scalar dz=(zMax_-a_)/bins_;
                label first=max(label(0), label(std::floor((low-a_)/dz)));
                label last=min(bins_-1, label(std::floor((high-a_)/dz)));
                for (label bin=first; bin<=last; ++bin)
                {
                    auto polygon=clip(strip,1,a_+bin*dz,true);
                    polygon=clip(polygon,1,a_+(bin+1)*dz,false);
                    for (label j=1; j+1<polygon.size(); ++j)
                    {
                        scalar area=0.5*mag((polygon[j]-polygon[0])^(polygon[j+1]-polygon[0]));
                        if (area>VSMALL)
                        {
                            pieces_.append(Piece{polygon[0],polygon[j],polygon[j+1],facei,area});
                            areaTotal+=area;
                        }
                    }
                }
            }
        }
        reduce(areaTotal,sumOp<scalar>());
        if (mag(areaTotal-width_*(zMax_-a_))>1e-6*width_*(zMax_-a_))
            FatalErrorInFunction << "The entire rectangular feed transect must be covered "
                << "by the selected inlet patch; covered area=" << areaTotal << exit(FatalError);
    }

    void profileWeights()
    {
        const auto& cloud=this->owner();
        const auto& mesh=cloud.mesh();
        // Estimate terminal settling using EXACTLY v1912 sphereDrag's Cd(Re).
        // Balancing drag and submerged weight avoids an inconsistent Rouse
        // velocity obtained from a different grain-shape drag correlation.
        scalar nu=gAverage(cloud.mu().primitiveField()/cloud.rho().primitiveField());
        scalar rhoFluid=gAverage(cloud.rho().primitiveField());
        scalar rhoParticle=cloud.constProps().rho0();
        if (!(nu>0 && rhoParticle>rhoFluid))
            FatalErrorInFunction << "Positive carrier viscosity and sand density above water required" << exit(FatalError);
        if (!this->coeffDict().found("settlingVelocity"))
        {
            scalar rhs=4.0/3.0*(rhoParticle/rhoFluid-1)*mag(cloud.g().value())*d_;
            scalar lo=0, hi=1;
            auto drag=[&](scalar speed)
            {
                scalar Re=speed*d_/nu;
                scalar CdRe=Re>1000 ? scalar(0.424)*Re
                    : scalar(24)*(1+Foam::pow(Re,scalar(2.0/3.0))/6);
                return CdRe*speed*nu/d_;
            };
            while(drag(hi)<rhs) hi*=2;
            for(label iter=0;iter<80;++iter)
            {
                scalar mid=0.5*(lo+hi);
                if(drag(mid)<rhs) lo=mid; else hi=mid;
            }
            ws_=0.5*(lo+hi);
        }
        else ws_=readScalar(this->coeffDict().lookup("settlingVelocity"));
        uStar_=prescribedShear_;
        if (profile_=="Rouse" && shearModel_=="localBed")
        {
            label bedID=mesh.boundaryMesh().findPatchID(bedPatchName_);
            if (bedID<0 || !isA<wallPolyPatch>(mesh.boundaryMesh()[bedID]))
                FatalErrorInFunction << "bedPatch must identify a wall for local shear" << exit(FatalError);
            const auto& turbulence=mesh.template lookupObject<incompressible::turbulenceModel>
                (turbulenceModel::propertiesName);
            const volSymmTensorField stress(turbulence.devReff());
            scalar weightedStress=0, sampleArea=0;
            forAll(mesh.boundary()[bedID], facei)
            {
                vector r=mesh.Cf().boundaryField()[bedID][facei]-origin_;
                scalar downstream=r & inward_;
                if (downstream>=0 && downstream<=shearLength_
                 && mag(r & widthDir_)<=width_/2)
                {
                    vector n=mesh.Sf().boundaryField()[bedID][facei]
                        /mesh.magSf().boundaryField()[bedID][facei];
                    vector traction=n & stress.boundaryField()[bedID][facei];
                    scalar area=mesh.magSf().boundaryField()[bedID][facei];
                    weightedStress+=area*mag(traction-n*(n & traction));
                    sampleArea+=area;
                }
            }
            reduce(sampleArea,sumOp<scalar>());
            reduce(weightedStress,sumOp<scalar>());
            if (!(sampleArea>0))
                FatalErrorInFunction << "No bed face centres lie within the local shear sampling area" << exit(FatalError);
            uStar_=Foam::sqrt(weightedStress/sampleArea);
        }
        if (profile_=="Rouse" && !(uStar_>SMALL))
            FatalErrorInFunction << "Rouse profile needs nonzero uStar; use prescribed shear "
                << "or an established bed flow" << exit(FatalError);
        exponent_=profile_=="Rouse" ? ws_/(kappa_*beta_*uStar_) : 0;
        if (!std::isfinite(exponent_))
            FatalErrorInFunction << "Invalid Rouse exponent" << exit(FatalError);
        cumulative_.setSize(pieces_.size());
        scalar total=0;
        forAll(pieces_, i)
        {
            const Piece& t=pieces_[i];
            scalar z=(t.a.y()+t.b.y()+t.c.y())/3;
            // C(z)/C(a) = [a(H-z)/(z(H-a))]^P. Positive cutoffs
            // exclude the singular bed and free-surface endpoints.
            scalar relative=std::exp(exponent_*std::log(a_*(depth_-z)/(z*(depth_-a_))));
            vector initial=cloud.U().boundaryField()[patchID_][t.face];
            if (velocityModel_=="equilibrium") initial-=ws_*up_;
            scalar normalSpeed=initial & inward_;
            scalar weight=normalSpeed>0 ? t.area*relative
                *((profile_=="Rouse" && fluxWeighted_) ? normalSpeed : scalar(1)) : 0;
            total+=weight;
            cumulative_[i]=total;
        }
        // One total per rank allows globally weighted selection without
        // serializing face geometry. A rank with no inlet faces has zero weight.
        procTotals_.setSize(Pstream::nProcs(),0);
        forAll(procTotals_, proci)
        {
            scalar rankTotal=Pstream::myProcNo()==proci ? total : 0;
            reduce(rankTotal,sumOp<scalar>());
            procTotals_[proci]=rankTotal;
        }
        if (!(sum(procTotals_)>VSMALL))
            FatalErrorInFunction << "No positive inward flux supports the transect feed; "
                << "check inlet velocity and Rouse resolution" << exit(FatalError);
        Info<< "Sand feed profile: " << profile_ << " ws=" << ws_
            << " uStar=" << uStar_ << " RouseP=" << exponent_
            << " massFeedRate=" << feedRate_ << " kg/s" << endl;
    }

protected:
    virtual bool prepareForNextTimeStep(scalar time,label& n,scalar& volumeFraction)
    {
        bool ready=InjectionModel<CloudType>::prepareForNextTimeStep(time,n,volumeFraction);
        if (ready)
        {
            // Low-rate batches contain accumulated feed mass, but are released
            // in the CURRENT step. Do not backdate parcels to an old feed batch.
            this->time0_=max(this->SOI_,time-this->owner().db().time().deltaTValue());
        }
        return ready;
    }
public:
    TypeName("sandTransectInjection");
    SandTransectInjection(const dictionary& dict, CloudType& owner, const word& modelName)
    : InjectionModel<CloudType>(nativeDictionary(dict),owner,modelName,typeName),
      patchName_(dict.getWord("patch")), bedPatchName_(dict.lookupOrDefault<word>("bedPatch","")),
      profile_(dict.lookupOrDefault<word>("profile","uniform")),
      shearModel_(dict.lookupOrDefault<word>("shearVelocityModel","localBed")),
      velocityModel_(dict.lookupOrDefault<word>("initialVelocity","carrier")),
      patchID_(owner.mesh().boundaryMesh().findPatchID(patchName_)),
      bins_(dict.lookupOrDefault<label>("profileBins",128)),
      seed_(dict.lookupOrDefault<label>("seed",12345)),
      origin_(dict.lookup("origin")), widthDir_(dict.lookup("widthDirection")),
      up_(-owner.g().value()/mag(owner.g().value())), inward_(Zero), selectedU_(Zero),
      width_(dict.getScalar("width")), depth_(dict.getScalar("depth")),
      a_(dict.getScalar("referenceHeight")), zMax_(dict.getScalar("maximumHeight")),
      offset_(dict.lookupOrDefault<scalar>("inwardOffset",1e-7)),
      duration_(dict.getScalar("duration")), feedRate_(dict.getScalar("massFeedRate")),
      parcelRate_(dict.getScalar("parcelsPerSecond")),
      d_(dict.lookupOrDefault<scalar>("d50",1.25e-4)),
      kappa_(dict.lookupOrDefault<scalar>("kappa",0.41)),
      beta_(dict.lookupOrDefault<scalar>("beta",1)),
      shearLength_(dict.lookupOrDefault<scalar>("shearSampleLength",0.1)),
      prescribedShear_(dict.lookupOrDefault<scalar>("uStar",0)),
      ws_(0), uStar_(0), exponent_(0),
      fluxWeighted_(dict.lookupOrDefault<bool>("fluxWeighted",true)),
      writeSamples_(dict.lookupOrDefault<bool>("writeSamples",false))
    {
        if (patchID_<0 || owner.mesh().boundary()[patchID_].coupled()
         || isA<wallPolyPatch>(owner.mesh().boundaryMesh()[patchID_]))
            FatalErrorInFunction << "patch must be an open, uncoupled inlet patch" << exit(FatalError);
        if (owner.mesh().nGeometricD()!=3)
            FatalErrorInFunction << "A finite-width vertical transect requires a 3-D mesh" << exit(FatalError);
        const scalar inputs[]={width_,depth_,a_,zMax_,offset_,duration_,feedRate_,parcelRate_,
            d_,kappa_,beta_,shearLength_,prescribedShear_,mag(origin_),mag(widthDir_),
            feedRate_*duration_,this->SOI_,mag(owner.g().value())};
        for (scalar value:inputs)
            if (!std::isfinite(value))
                FatalErrorInFunction << "Feed inputs must be finite" << exit(FatalError);
        if (!(width_>0 && depth_>0 && a_>0 && zMax_>a_ && zMax_<depth_
           && offset_>0 && duration_>0 && feedRate_>=0 && parcelRate_>0
           && d_>0 && kappa_>0 && beta_>0 && shearLength_>0 && bins_>=8 && bins_<=4096))
            FatalErrorInFunction << "Invalid feed dimensions, rate, profile cutoffs or resolution" << exit(FatalError);
        if (dict.found("settlingVelocity"))
        {
            scalar ws=readScalar(dict.lookup("settlingVelocity"));
            if (!(ws>=0 && std::isfinite(ws)))
                FatalErrorInFunction << "settlingVelocity must be finite and nonnegative" << exit(FatalError);
        }
        if (!(mag(widthDir_)>SMALL))
            FatalErrorInFunction << "widthDirection must be nonzero" << exit(FatalError);
        widthDir_/=mag(widthDir_);
        if (mag(widthDir_ & up_)>1e-6)
            FatalErrorInFunction << "widthDirection must be horizontal" << exit(FatalError);
        if (profile_!="uniform" && profile_!="Rouse")
            FatalErrorInFunction << "profile must be uniform or Rouse" << exit(FatalError);
        if (shearModel_!="localBed" && shearModel_!="constant")
            FatalErrorInFunction << "shearVelocityModel must be localBed or constant" << exit(FatalError);
        if (profile_=="Rouse" && shearModel_=="constant" && !(prescribedShear_>0))
            FatalErrorInFunction << "Positive uStar required for constant Rouse shear" << exit(FatalError);
        if (velocityModel_!="carrier" && velocityModel_!="equilibrium")
            FatalErrorInFunction << "initialVelocity must be carrier or equilibrium" << exit(FatalError);
        if (!owner.solution().transient() || owner.solution().coupled())
            FatalErrorInFunction << "Transect feeding requires a transient uncoupled cloud" << exit(FatalError);
        this->volumeTotal_=feedRate_*duration_/owner.constProps().rho0();
        // Refuse to replay feed history when native restart metadata is absent.
        if (owner.db().time().value()>this->SOI_+SMALL
         && this->template getModelProperty<scalar>("timeStep0",-GREAT)==-GREAT)
            FatalErrorInFunction << "Restart requires sandCloudOutputProperties injection state" << exit(FatalError);
        geometry();
    }
    SandTransectInjection(const SandTransectInjection& other)=default;
    virtual autoPtr<InjectionModel<CloudType>> clone() const
    { return autoPtr<InjectionModel<CloudType>>(new SandTransectInjection(*this)); }
    virtual scalar timeEnd() const { return this->SOI_+duration_; }
    virtual void updateMesh() { geometry(); }
    virtual label parcelsToInject(scalar t0, scalar t1)
    {
        scalar start=max(scalar(0),t0), end=min(duration_,t1);
        if (!(end>start) || feedRate_==0) return 0;
        // Native base holds elapsed feed volume until at least one parcel is
        // due. A final partial batch is flushed at duration instead of lost.
        scalar n0=std::floor(start*parcelRate_+1e-9);
        scalar n1=std::floor(end*parcelRate_+1e-9);
        scalar count=n1-n0;
        if (end>=duration_-SMALL && count<1) count=1;
        if (count>scalar(labelMax-this->parcelsAddedTotal_))
            FatalErrorInFunction << "Parcel batch exceeds label capacity" << exit(FatalError);
        label n=label(count);
        if(n>0) profileWeights();
        return n;
    }
    virtual scalar volumeToInject(scalar t0, scalar t1)
    {
        return feedRate_*max(scalar(0),min(duration_,t1)-max(scalar(0),t0))
            /this->owner().constProps().rho0();
    }
    virtual void setPositionAndCell(label parcelI,label,const scalar,
        vector& position,label& cellOwner,label& tetFacei,label& tetPti)
    {
        label id=this->parcelsAddedTotal_+parcelI;
        scalar target=random01(id,0)*sum(procTotals_);
        label selectedProc=0;
        while(selectedProc+1<procTotals_.size() && target>=procTotals_[selectedProc])
            target-=procTotals_[selectedProc++];
        position=Zero;
        selectedU_=Zero;
        if (Pstream::myProcNo()==selectedProc)
        {
            label lo=0,hi=cumulative_.size()-1;
            while(lo<hi) { label mid=(lo+hi)/2; if(cumulative_[mid]>target) hi=mid; else lo=mid+1; }
            const Piece& piece=pieces_[lo];
            // Uniform triangle sampling by square-root barycentric transform.
            scalar r=Foam::sqrt(random01(id,1)), s=random01(id,2);
            vector local=(1-r)*piece.a+r*(1-s)*piece.b+r*s*piece.c;
            position=origin_+local.x()*widthDir_+local.y()*up_+offset_*inward_;
            selectedU_=this->owner().U().boundaryField()[patchID_][piece.face];
            if(velocityModel_=="equilibrium") selectedU_-=ws_*up_;
        }
        reduce(position,sumOp<vector>());
        reduce(selectedU_,sumOp<vector>());
        if(writeSamples_)
            Info<< "Sand sample: id=" << id << " position=" << position << endl;
        // Native lookup handles ownership at processor interfaces and assigns
        // the barycentric tetrahedron required by the native particle tracker.
        this->findCellAtPosition(cellOwner,tetFacei,tetPti,position,true);
    }
    virtual void setProperties(label,label,scalar,typename CloudType::parcelType& parcel)
    { parcel.U()=selectedU_; parcel.d()=d_; }
    virtual bool fullyDescribed() const { return false; }
    virtual bool validInjection(label) { return true; }
};
}
#endif
