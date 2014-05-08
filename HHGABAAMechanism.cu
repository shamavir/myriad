/**
   @file    HHNaCurrMechanism.cu
 
   @brief   Hodgkin-Huxley Sodium Mechanism CUDA implementation file.
 
   @details Defines the Hodgkin-Huxley Sodium Mechanism CUDA implementation for Myriad
 
   @author  Pedro Rittner
 
   @date    April 23, 2014
 */
#include <stdio.h>

#include <cuda_runtime.h>

extern "C"
{
    #include "myriad_debug.h"
	#include "MyriadObject.h"
    #include "Compartment.h"
	#include "HHSomaCompartment.h"
	#include "Mechanism.h"
	#include "HHGABAAMechanism.h"
}

#include "HHSomaCompartment.cuh"
#include "HHGABAAMechanism.cuh"

__device__ __constant__ struct HHGABAAMechanism* HHGABAAMechanism_dev_t;
__device__ __constant__ struct HHGABAAMechanismClass* HHGABAAMechanismClass_dev_t;

__device__ double HHGABAAMechanism_cuda_mech_fun(
    void* _self,
	void* pre_comp,
	void* post_comp,
	const double dt,
	const double global_time,
	const unsigned int curr_step
	)
{
	struct HHGABAAMechanism* self = (struct HHGABAAMechanism*) _self;
	const struct HHSomaCompartment* c1 = (const struct HHSomaCompartment*) pre_comp;
	const struct HHSomaCompartment* c2 = (const struct HHSomaCompartment*) post_comp;

	//	Channel dynamics calculation
	const double pre_vm = c1->soma_vm[curr_step-1];
	const double post_vm = c2->soma_vm[curr_step-1];
	const double prev_g_s = self->g_s[curr_step-1];

	const double fv = 1.0 / (1.0 + exp((pre_vm - self->theta)/-self->sigma));
	self->g_s[curr_step] += dt * (self->tau_alpha * fv * (1.0 - prev_g_s) - self->tau_beta * prev_g_s);

	return -self->g_max * prev_g_s * (post_vm - self->gaba_rev);
}

__device__ mech_fun_t HHGABAAMechanism_mech_fxn_t = HHGABAAMechanism_cuda_mech_fun;