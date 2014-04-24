#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <string.h>

#ifdef CUDA
#include <vector_types.h>
#include <cuda_runtime.h>
#include <cuda_runtime_api.h>
#endif

// Myriad C API Headers
extern "C"
{
	#include "myriad_debug.h"
    #include "MyriadObject.h"
	#include "Mechanism.h"
	#include "Compartment.h"
	#include "HHSomaCompartment.h"
	#include "HHLeakMechanism.h"
}

#ifdef CUDA
#include "MyriadObject.cuh"
#include "Mechanism.cuh"
#include "Compartment.cuh"
#endif

///////////////////
// Test CUDA OOP //
///////////////////

#ifdef CUDA
__global__ void cuda_oop_test(void* c_obj)
{
    printf("\tsize(GPU): %lu\n", cuda_myriad_size_of(c_obj));
    printf("\tis_a: %s\n", cuda_myriad_is_a(c_obj, MyriadObject_dev_t) ? "TRUE" : "FALSE");
    printf("\tis_of: %s\n", cuda_myriad_is_of(c_obj, MyriadObject_dev_t) ? "TRUE" : "FALSE");
}
#endif

static int cuda_oop()
{
	#ifdef CUDA
    initCUDAObjects();
	#endif
    
    void* my_obj = myriad_new(MyriadObject);
	assert(my_obj);

	#ifdef CUDA
    void* my_cuda_obj = myriad_cudafy(my_obj, 0);

    const int nThreads = 1; // NUM_CUDA_THREADS;
    const int nBlocks = 1;

    dim3 dimGrid(nBlocks);
    dim3 dimBlock(nThreads);

    cuda_oop_test<<<dimGrid, dimBlock>>>(my_cuda_obj);
	CUDA_CHECK_RETURN(cudaDeviceSynchronize());
    CUDA_CHECK_RETURN(cudaGetLastError());
	
	CUDA_CHECK_RETURN(cudaFree(my_cuda_obj));
    assert(myriad_dtor(my_obj) == EXIT_SUCCESS);

    cudaDeviceReset();
	#endif

    return EXIT_SUCCESS;
}

////////////////////
// Test Mechanism //
////////////////////

#ifdef CUDA
__global__ void cuda_mechansim_test(void* obj)
{
	struct Mechanism* self = (struct Mechanism*) obj;
	struct MechanismClass* self_c = (struct MechanismClass*) cuda_myriad_class_of(self);
	printf("\tMy ptr: %p\n", self);
	printf("\tMy ID: %i\n", self->source_id);
	printf("\tMy class: %p\n", self->_.m_class);
	printf("\tGPU, my size: %lu\n", cuda_myriad_size_of(obj));
	printf("\tMechanism fxn: %p\n", self_c->m_mech_fxn);
	printf("\tMechanism fxn invocation: %f\n", self_c->m_mech_fxn(self, NULL, NULL, 0.0, 0.0, 0));
	printf("\tMechanism fxn indirect call: %f\n", cuda_mechanism_fxn(self, NULL, NULL, 0.0, 0.0, 0));
}
#endif

static int mechanism_test()
{
	#ifdef CUDA
	{
	    initCUDAObjects();
    	initMechanism(1);
    }
	#else
	{
		initMechanism(0);
	}
	#endif

	void* mech_obj = NULL, *dev_mech_obj = NULL;

	mech_obj = myriad_new(Mechanism, 1);

	UNIT_TEST_VAL_EQ(myriad_size_of(mech_obj), sizeof(struct Mechanism));

	mechanism_fxn(mech_obj, NULL, NULL, 0, 0, 0);

	#ifdef CUDA
	{
	    dev_mech_obj = myriad_cudafy(mech_obj, 0);

        const int nThreads = 1; // NUM_CUDA_THREADS;
        const int nBlocks = 1;

        dim3 dimGrid(nBlocks);
        dim3 dimBlock(nThreads);

        cuda_mechansim_test<<<dimGrid, dimBlock>>>(dev_mech_obj); // Not an error
	    CUDA_CHECK_RETURN(cudaDeviceSynchronize());
        CUDA_CHECK_RETURN(cudaGetLastError());

    	CUDA_CHECK_RETURN(cudaFree(dev_mech_obj));
	    assert(!myriad_dtor(mech_obj));

        cudaDeviceReset();
    }
	#endif

    return EXIT_SUCCESS;
}

//////////////////////
// Test Compartment //
//////////////////////
#ifdef CUDA
__global__ void cuda_compartment_test(void* obj)
{
	struct Compartment* self = (struct Compartment*) obj;
	struct CompartmentClass* self_c = (struct CompartmentClass*) cuda_myriad_class_of(self);
	printf("\tMy ptr: %p\n", self);
	printf("\tMy ID: %i\n", self->id);
	printf("\tMy class: %p\n", self->_.m_class);
	printf("\tGPU, my size: %lu\n", cuda_myriad_size_of(obj));
	printf("\tCompartment fxn: %p\n", self_c->m_comp_fxn);
	printf("\tCompartment fxn invocation: "); self_c->m_comp_fxn(self, NULL, 0.0, 0.0, 0);
	printf("\tCompartent fxn indirect call: "); cuda_simul_fxn(self, NULL, 0.0, 0.0, 0);
}
#endif

static int compartment_test()
{
	#ifdef CUDA
	{
	    initCUDAObjects();
	    initCompartment(1);
    }
    #else
	{
		initCompartment(0);
	}
	#endif

	void* comp_obj = NULL;

	comp_obj = myriad_new(Compartment, 5, 42, NULL, 1.0);

	// Make sure we haven't overallocated
	UNIT_TEST_VAL_EQ(myriad_size_of(comp_obj), sizeof(struct Compartment));

	simul_fxn(comp_obj, NULL, 0.0, 0.0, 0);

	#ifdef CUDA
	{
	    void* dev_comp_obj = myriad_cudafy(comp_obj, 0);

        const int nThreads = 1; // NUM_CUDA_THREADS;
        const int nBlocks = 1;

        dim3 dimGrid(nBlocks);
        dim3 dimBlock(nThreads);

        cuda_compartment_test<<<dimGrid, dimBlock>>>(dev_comp_obj);
	    CUDA_CHECK_RETURN(cudaDeviceSynchronize());
        CUDA_CHECK_RETURN(cudaGetLastError());

	    CUDA_CHECK_RETURN(cudaFree(dev_comp_obj));
     	assert(!myriad_dtor(comp_obj));

        cudaDeviceReset();
    }
	#endif

	return EXIT_SUCCESS;
}

//////////////////////////////
// Test Device Symbol Malloc /
//////////////////////////////

#ifdef CUDA
__device__ float* my_float_ptr = NULL;

__global__ void cuda_dev_malloc_test()
{
	printf("my_float_ptr: %f\n", my_float_ptr[0]);
}
#endif

static int cuda_symbol_malloc()
{
	float* host_float_ptr = NULL, host_float_val = 5.0;

	#ifdef CUDA
	CUDA_CHECK_RETURN(cudaMalloc((void**)&host_float_ptr, sizeof(float)));
	CUDA_CHECK_RETURN(cudaMemcpy(host_float_ptr, &host_float_val, sizeof(float), cudaMemcpyHostToDevice));
	CUDA_CHECK_RETURN(cudaMemcpyToSymbol(my_float_ptr, &host_float_ptr, sizeof(float*), size_t(0), cudaMemcpyHostToDevice));
	
    const int nThreads = 1; // NUM_CUDA_THREADS;
    const int nBlocks = 1;

    dim3 dimGrid(nBlocks);
    dim3 dimBlock(nThreads);

    cuda_dev_malloc_test<<<dimGrid, dimBlock>>>(); 
	CUDA_CHECK_RETURN(cudaDeviceSynchronize());
    CUDA_CHECK_RETURN(cudaGetLastError());

    cudaDeviceReset();
	#endif

    return EXIT_SUCCESS;
}

///////////////////////
// HHCompartmentTest //
///////////////////////

#ifdef CUDA
__global__ void cuda_hh_compartment_test(void* hh_comp_obj)
{
	struct Compartment* self = (struct Compartment*) hh_comp_obj;
	struct CompartmentClass* self_c = (struct CompartmentClass*) cuda_myriad_class_of(self);
	printf("\tMy ptr: %p\n", self);
	printf("\tMy ID: %i\n", self->id);
	printf("\tMy class: %p\n", self->_.m_class);
	printf("\tGPU, my size: %lu\n", cuda_myriad_size_of(hh_comp_obj));
	printf("\tCompartment fxn: %p\n", self_c->m_comp_fxn);
	printf("\tCompartment fxn invocation: "); self_c->m_comp_fxn(self, NULL, 0.0, 0.0, 0);
	printf("\tCompartent fxn indirect call: "); cuda_simul_fxn(self, NULL, 0.0, 0.0, 0);
}
#endif

static int HHCompartmentTest()
{
	#ifdef CUDA
	{
	    initCUDAObjects();
		initMechanism(1);
		initHHLeakMechanism(1);
    	initCompartment(1);
	    initHHSomaCompartment(1);
    }
	#else
	{
		initMechanism(0);
		initHHLeakMechanism(0);
    	initCompartment(0);
	    initHHSomaCompartment(0);
	}
	#endif

	const double G_LEAK = 0.8;
	const double E_REV = -65.0;
	const double CM = 600.0;

	const int SIMUL_LEN = 50;
	const double DT = 0.001;

	void** network = (void**) calloc(1, sizeof(void*));

	void* hh_comp_obj = myriad_new(HHSomaCompartment, 0, 0, NULL, SIMUL_LEN, NULL, CM);
	void* hh_leak_mech = myriad_new(HHLeakMechanism, 0, G_LEAK, E_REV);

	network[0] = hh_comp_obj;
	
	// Add mechanism to compartment
	assert(EXIT_SUCCESS == add_mechanism(hh_comp_obj, hh_leak_mech));
	const unsigned int CURR_STEP = 1;
	simul_fxn(hh_comp_obj, network, DT, 0.0, CURR_STEP);

	struct HHSomaCompartment* hh_comp_obj_s = (struct HHSomaCompartment*) hh_comp_obj;
	for (unsigned int i = 0; i < hh_comp_obj_s->soma_vm_len; i++)
	{
		printf("VM at step %i is %f\n", i, hh_comp_obj_s->soma_vm[i]);
	}

	#ifdef CUDA
	{
    	void* dev_hh_comp_obj = myriad_cudafy(hh_comp_obj, 0);
    
        const int nThreads = 1; // NUM_CUDA_THREADS;
        const int nBlocks = 1;

        dim3 dimGrid(nBlocks);
        dim3 dimBlock(nThreads);

        cuda_hh_compartment_test<<<dimGrid, dimBlock>>>(dev_hh_comp_obj);
	    CUDA_CHECK_RETURN(cudaDeviceSynchronize());
        CUDA_CHECK_RETURN(cudaGetLastError());

        cudaDeviceReset();
	}
	#endif

	// Free
	assert(EXIT_SUCCESS == myriad_dtor(hh_comp_obj));

    return EXIT_SUCCESS;
}

///////////////////
// Main function //
///////////////////
int main(int argc, char const *argv[])
{
    puts("Hello World!\n");

    UNIT_TEST_FUN(cuda_oop);
	UNIT_TEST_FUN(cuda_symbol_malloc);
	UNIT_TEST_FUN(mechanism_test);
	UNIT_TEST_FUN(compartment_test);
	UNIT_TEST_FUN(HHCompartmentTest);

	HHCompartmentTest();

    puts("\nDone.");

    return EXIT_SUCCESS;
}
