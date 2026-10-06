// uma_probe.cu — prove sm_121 codegen works and show unified-memory behaviour on GB10.
// Build: nvcc -O2 -gencode arch=compute_121,code=sm_121 -o uma_probe uma_probe.cu
#include <cstdio>
#include <cstdlib>
#include <cuda_runtime.h>

#define CK(x) do { cudaError_t e = (x); if (e != cudaSuccess) { \
  fprintf(stderr, "CUDA error %s at %s:%d\n", cudaGetErrorString(e), __FILE__, __LINE__); return 2; } } while (0)

__global__ void saxpy(size_t n, float a, const float* x, float* y) {
  size_t i = blockIdx.x * (size_t)blockDim.x + threadIdx.x;
  if (i < n) y[i] = a * x[i] + y[i];
}

int main() {
  cudaDeviceProp p; CK(cudaGetDeviceProperties(&p, 0));
  size_t freeB, totalB; CK(cudaMemGetInfo(&freeB, &totalB));
  printf("device=%s cc=%d.%d sms=%d integrated=%d concurrentManaged=%d pageableAccess=%d\n",
         p.name, p.major, p.minor, p.multiProcessorCount, p.integrated,
         p.concurrentManagedAccess, p.pageableMemoryAccess);
  printf("cudaMemGetInfo free=%.1fGiB total=%.1fGiB (on UMA this tracks host memory)\n",
         freeB / 1073741824.0, totalB / 1073741824.0);

  const size_t n = 1ull << 28;               // 268M floats = 1 GiB per array
  float *x, *y;
  // Plain malloc'd memory is GPU-accessible on a coherent system when pageableMemoryAccess=1.
  // We use cudaMallocManaged for portability.
  CK(cudaMallocManaged(&x, n * sizeof(float)));
  CK(cudaMallocManaged(&y, n * sizeof(float)));
  for (size_t i = 0; i < n; ++i) { x[i] = 1.0f; y[i] = 2.0f; }

  cudaEvent_t a, b; cudaEventCreate(&a); cudaEventCreate(&b);
  cudaEventRecord(a);
  saxpy<<<(unsigned)((n + 255) / 256), 256>>>(n, 3.0f, x, y);
  cudaEventRecord(b); CK(cudaEventSynchronize(b));
  CK(cudaGetLastError());
  float ms; cudaEventElapsedTime(&ms, a, b);
  double gbs = 3.0 * n * sizeof(float) / (ms / 1e3) / 1e9;   // 2 reads + 1 write

  size_t bad = 0; for (size_t i = 0; i < n; i += 4096) bad += (y[i] != 5.0f);
  printf("saxpy n=%zu time=%.2fms effective_bw=%.1fGB/s check=%s\n", n, ms, gbs, bad ? "FAIL" : "PASS");
  cudaFree(x); cudaFree(y);
  return bad ? 1 : 0;
}
