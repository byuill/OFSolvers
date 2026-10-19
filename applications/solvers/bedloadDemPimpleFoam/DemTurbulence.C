/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "DemTurbulence.H"
makeBaseTurbulenceModel(volScalarField,geometricOneField,incompressibleTurbulenceModel,
    PhaseIncompressibleTurbulenceModel,transportModel);
#include "Stokes.H"
makeTemplatedTurbulenceModel(transportModelPhaseIncompressibleTurbulenceModel,laminar,Stokes);
#include "kOmegaSST.H"
makeTemplatedTurbulenceModel(transportModelPhaseIncompressibleTurbulenceModel,RAS,kOmegaSST);
#include "kEpsilon.H"
makeTemplatedTurbulenceModel(transportModelPhaseIncompressibleTurbulenceModel,RAS,kEpsilon);
#include "Smagorinsky.H"
makeTemplatedTurbulenceModel(transportModelPhaseIncompressibleTurbulenceModel,LES,Smagorinsky);
#include "WALE.H"
makeTemplatedTurbulenceModel(transportModelPhaseIncompressibleTurbulenceModel,LES,WALE);
