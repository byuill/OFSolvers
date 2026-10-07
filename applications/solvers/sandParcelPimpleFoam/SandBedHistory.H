/* SPDX-License-Identifier: GPL-3.0-or-later
 * Local, restartable history keyed by native (origProc, origId) particle IDs.
 * History accompanies parcels on MPI transfer; it is not globally replicated.
 */
#ifndef SandBedHistory_H
#define SandBedHistory_H
#include "localIOdictionary.H"
#include "basicKinematicParcel.H"
namespace Foam
{
class SandBedHistory
{
    // Unlike ordinary IOdictionary, this reads each processor's own inventory
    // instead of broadcasting rank zero's dictionary during restart.
    localIOdictionary state_;
    dictionary records_;
    scalar releasedMass_;
    label releaseEvents_;
    bool loaded_;
public:
    static word key(const basicKinematicParcel& p)
    { return word("p"+Foam::name(p.origProc())+"_i"+Foam::name(p.origId())); }
    SandBedHistory(const fvMesh& mesh,bool initializeOnRestart)
    : state_(IOobject("sandResuspensionState",mesh.time().timeName(),
        "uniform/lagrangian/sandCloud",mesh,IOobject::READ_IF_PRESENT,
        IOobject::NO_WRITE,false)),
      releasedMass_(0),releaseEvents_(0),loaded_(state_.found("schemaVersion"))
    {
        if (loaded_)
        {
            if (state_.get<label>("schemaVersion")!=1
             || state_.get<label>("nProcs")!=Pstream::nProcs()
             || state_.get<label>("procNo")!=Pstream::myProcNo()
             || state_.get<label>("nCells")!=mesh.nCells()
             || mag(state_.get<scalar>("stateTime")-mesh.time().value())
                >1e-10*max(scalar(1),mag(mesh.time().value())))
                FatalErrorInFunction << "Resuspension restart requires the same mesh, "
                    << "decomposition and matching time directory" << exit(FatalError);
            records_=state_.subDict("records");
            releasedMass_=state_.get<scalar>("releasedMass");
            releaseEvents_=state_.get<label>("releaseEvents");
        }
        else if (mesh.time().value()>SMALL && !initializeOnRestart)
            FatalErrorInFunction << "Missing sandResuspensionState. To enable this process "
                << "on an older case explicitly set initializeHistoryOnRestart true" << exit(FatalError);
    }
    bool loaded() const { return loaded_; }
    dictionary& records() { return records_; }
    const dictionary& records() const { return records_; }
    scalar releasedMass() const { return releasedMass_; }
    label releaseEvents() const { return releaseEvents_; }
    void addRelease(scalar mass) { releasedMass_+=mass; ++releaseEvents_; }
    template<class ParcelList>
    dictionary take(const ParcelList& parcels)
    {
        dictionary packet;
        for (const auto& p:parcels)
        {
            word id=key(p);
            if (records_.found(id))
            {
                packet.add(id,records_.subDict(id));
                records_.remove(id);
            }
        }
        return packet;
    }
    void receive(const dictionary& packet)
    {
        for (const word& id:packet.toc())
        {
            if (records_.found(id))
                FatalErrorInFunction << "Duplicate particle bed history " << id << exit(FatalError);
            records_.add(id,packet.subDict(id));
        }
    }
    void write(const fvMesh& mesh)
    {
        state_.instance()=mesh.time().timeName();
        state_.set("schemaVersion",label(1));
        state_.set("stateTime",mesh.time().value());
        state_.set("nProcs",Pstream::nProcs());
        state_.set("procNo",Pstream::myProcNo());
        state_.set("nCells",mesh.nCells());
        state_.set("releasedMass",releasedMass_);
        state_.set("releaseEvents",releaseEvents_);
        state_.set("records",records_);
        // Hazard and event times must survive even when carrier output uses
        // the default six significant digits. Restore the user's precision
        // afterward so native field output is unaffected.
        unsigned int precision=IOstream::defaultPrecision(17);
        bool written=state_.regIOobject::write();
        IOstream::defaultPrecision(precision);
        if (!written)
            FatalErrorInFunction << "Cannot write sandResuspensionState" << exit(FatalError);
    }
};
}
#endif
