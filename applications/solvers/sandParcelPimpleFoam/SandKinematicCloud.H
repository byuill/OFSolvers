/* SPDX-License-Identifier: GPL-3.0-or-later
 * Transfer loop adapted from OpenFOAM v1912 Cloud::move, copyright
 * OpenFOAM Foundation/OpenCFD (see upstream Cloud.C and sibling COPYING).
 * v1912's injector tracks each new parcel over its partial first time step.
 * Resetting EVERY parcel's stepFraction after injection advances it twice.
 * Reset old parcels before injection, then preserve new parcel fractions.
 */
#ifndef SandKinematicCloud_H
#define SandKinematicCloud_H
#include <unordered_map>
#include "basicKinematicCloud.H"
#include "SandBedHistory.H"
#include "PstreamBuffers.H"
#include "processorPolyPatch.H"
#include "globalMeshData.H"
#include "UOPstream.H"
#include "UIPstream.H"
namespace Foam
{
class SandKinematicCloud : public basicKinematicCloud
{
    // Only needed during a single injection/tracking step; restart state stays
    // in the native cloud. Dead parcel keys are never dereferenced.
    std::unordered_map<const basicKinematicParcel*, scalar> birthFraction_;
    std::unordered_map<const basicKinematicParcel*, scalar> releaseFraction_;
    autoPtr<SandBedHistory> bedHistory_;
public:
    using basicKinematicCloud::basicKinematicCloud;
    typedef basicKinematicParcel ParticleType;

    void enableBedHistory(bool initializeOnRestart)
    { bedHistory_.reset(new SandBedHistory(this->mesh(),initializeOnRestart)); }
    SandBedHistory& bedHistory() { return bedHistory_(); }
    void scheduleRelease(ParticleType& parcel,scalar fraction)
    { releaseFraction_[&parcel]=fraction; }
    void preEvolve()
    {
        birthFraction_.clear();
        for (ParticleType& p : *this)
        {
            auto release=releaseFraction_.find(&p);
            p.stepFraction()=release==releaseFraction_.end() ? 0 : release->second;
        }
        releaseFraction_.clear();
        basicKinematicCloud::preEvolve();
    }
    void checkParcelProperties(ParticleType& parcel, scalar dt, bool fullyDescribed)
    {
        basicKinematicCloud::checkParcelProperties(parcel,dt,fullyDescribed);
        scalar carrierDt=this->mesh().time().deltaTValue();
        birthFraction_[&parcel]=max(scalar(0),1-dt/carrierDt);
        // v1912 inject() passes the REMAINING dt to parcel.move(). Therefore
        // its local progress must begin at zero, otherwise dt is reduced twice.
        parcel.stepFraction()=0;
    }
    void evolve()
    {
        if (this->solution().canEvolve())
        {
            ParticleType::trackingData td(*this);
            // Passing the derived type makes solve dispatch preEvolve/motion
            // to this wrapper while retaining native forces and injection.
            this->solve(*this, td);
        }
    }
    void motion(SandKinematicCloud& cloud, ParticleType::trackingData& td)
    {
        for (ParticleType& p : *this)
        {
            auto birth=birthFraction_.find(&p);
            if (birth!=birthFraction_.end())
            {
                // Convert progress from the partial injection interval to
                // the full carrier step before completing processor transfer.
                scalar f=birth->second;
                p.stepFraction()=f+(1-f)*p.stepFraction();
            }
        }
        td.part()=ParticleType::trackingData::tpLinearTrack;
        movePreservingFraction(cloud, td, this->solution().trackTime());
        this->updateCellOccupancy();
    }
    void movePreservingFraction
    (
        SandKinematicCloud& cloud, ParticleType::trackingData& td, scalar trackTime
    )
    {
    const polyBoundaryMesh& pbm = pMesh().boundaryMesh();
    const globalMeshData& pData = pMesh().globalData();

    // Which patches are processor patches
    const labelList& procPatches = pData.processorPatches();

    // Indexing of equivalent patch on neighbour processor into the
    // procPatches list on the neighbour
    const labelList& procPatchNeighbours = pData.processorPatchNeighbours();

    // Which processors this processor is connected to
    const labelList& neighbourProcs = pData[Pstream::myProcNo()];

    // Indexing from the processor number into the neighbourProcs list
    labelList neighbourProcIndices(Pstream::nProcs(), -1);

    forAll(neighbourProcs, i)
    {
        neighbourProcIndices[neighbourProcs[i]] = i;
    }

    // Existing parcels were reset BEFORE injection in preEvolve().
    // New parcels already advanced by InjectionModel retain their fraction.

    // List of lists of particles to be transferred for all of the
    // neighbour processors
    List<IDLList<ParticleType>> particleTransferLists
    (
        neighbourProcs.size()
    );

    // List of destination processorPatches indices for all of the
    // neighbour processors
    List<DynamicList<label>> patchIndexTransferLists
    (
        neighbourProcs.size()
    );

    // Allocate transfer buffers
    PstreamBuffers pBufs(Pstream::commsTypes::nonBlocking);

    // Clear the global positions as there are about to change
    // This fixed-mesh wrapper never uses cached globalPositions for mapping.
    // No mesh motion/topology mapping is supported.

    // While there are particles to transfer
    while (true)
    {
        particleTransferLists = IDLList<ParticleType>();
        forAll(patchIndexTransferLists, i)
        {
            patchIndexTransferLists[i].clear();
        }

        // Loop over all particles
        for (ParticleType& p : *this)
        {
            // Move the particle
            bool keepParticle = p.move(cloud, td, trackTime);

            // If the particle is to be kept
            // (i.e. it hasn't passed through an inlet or outlet)
            if (keepParticle)
            {
                if (td.switchProcessor)
                {
                    #ifdef FULLDEBUG
                    if
                    (
                        !Pstream::parRun()
                     || !p.onBoundaryFace()
                     || procPatchNeighbours[p.patch()] < 0
                    )
                    {
                        FatalErrorInFunction
                            << "Switch processor flag is true when no parallel "
                            << "transfer is possible. This is a bug."
                            << exit(FatalError);
                    }
                    #endif

                    const label patchi = p.patch();

                    const label n = neighbourProcIndices
                    [
                        refCast<const processorPolyPatch>
                        (
                            pbm[patchi]
                        ).neighbProcNo()
                    ];

                    p.prepareForParallelTransfer();

                    particleTransferLists[n].append(this->remove(&p));

                    patchIndexTransferLists[n].append
                    (
                        procPatchNeighbours[patchi]
                    );
                }
            }
            else
            {
                deleteParticle(p);
            }
        }

        if (!Pstream::parRun())
        {
            break;
        }


        // Clear transfer buffers
        pBufs.clear();

        // Stream into send buffers
        forAll(particleTransferLists, i)
        {
            if (particleTransferLists[i].size())
            {
                UOPstream particleStream
                (
                    neighbourProcs[i],
                    pBufs
                );

                particleStream
                    << patchIndexTransferLists[i]
                    << particleTransferLists[i];
                if (bedHistory_.valid())
                    particleStream << bedHistory_->take(particleTransferLists[i]);
            }
        }


        // Start sending. Sets number of bytes transferred
        labelList allNTrans(Pstream::nProcs());
        pBufs.finishedSends(allNTrans);


        bool transferred = false;

        for (const label n : allNTrans)
        {
            if (n)
            {
                transferred = true;
                break;
            }
        }
        reduce(transferred, orOp<bool>());

        if (!transferred)
        {
            break;
        }

        // Retrieve from receive buffers
        for (const label neighbProci : neighbourProcs)
        {
            label nRec = allNTrans[neighbProci];

            if (nRec)
            {
                UIPstream particleStream(neighbProci, pBufs);

                labelList receivePatchIndex(particleStream);

                IDLList<ParticleType> newParticles
                (
                    particleStream,
                    typename ParticleType::iNew(pMesh())
                );

                if (bedHistory_.valid())
                {
                    dictionary incoming(particleStream);
                    bedHistory_->receive(incoming);
                }
                label pI = 0;

                for (ParticleType& newp : newParticles)
                {
                    label patchi = procPatches[receivePatchIndex[pI++]];

                    newp.correctAfterParallelTransfer(patchi, td);

                    addParticle(newParticles.remove(&newp));
                }
            }
        }
    }
}
};
}
#endif
