# llama.cpp server built for the GB10 (Chapter 07). Build ON the Spark (arm64 + sm_121),
# then import it into the kubeadm node's containerd (namespace k8s.io):
#   docker build -t spark-local/llama.cpp:server-sm121 --build-arg LLAMACPP_REF=b6500 .
#   docker save spark-local/llama.cpp:server-sm121 | sudo ctr -n k8s.io images import -
# With dgx-spark-2 joined, import it there too (rpc-2spark.yaml runs on both).
FROM nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 AS build
ARG LLAMACPP_REF=b6500
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential libcurl4-openssl-dev ca-certificates \
 && git clone --depth 1 --branch ${LLAMACPP_REF} https://github.com/ggml-org/llama.cpp /src
WORKDIR /src
# No GPU driver exists during `docker build`, so libcuda.so.1 can't be found when the tools are
# linked; let the linker leave it for run time (the NVIDIA runtime mounts it into the container),
# as llama.cpp's own CUDA Dockerfile does. Collect every shared library wherever this ref puts it.
RUN cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 -DGGML_RPC=ON -DLLAMA_CURL=ON \
      -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined \
 && cmake --build build --config Release -j"$(nproc)" --target llama-server llama-cli rpc-server llama-bench \
 && mkdir -p /out/lib && find build -name '*.so*' -exec cp -P {} /out/lib/ \;

FROM nvcr.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends libcurl4 libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bin/ /usr/local/bin/
COPY --from=build /out/lib/ /usr/local/lib/
RUN ldconfig
ENTRYPOINT ["llama-server"]
