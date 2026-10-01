# llama.cpp server built for the GB10 (Volume 17). Build ON the Spark:
#   docker build -t spark-local/llama.cpp:server-sm121 --build-arg LLAMACPP_REF=b6500 .
#   docker save spark-local/llama.cpp:server-sm121 | sudo k3s ctr images import -
FROM nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 AS build
ARG LLAMACPP_REF=b6500
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential libcurl4-openssl-dev ca-certificates \
 && git clone --depth 1 --branch ${LLAMACPP_REF} https://github.com/ggml-org/llama.cpp /src
WORKDIR /src
RUN cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 -DGGML_RPC=ON -DLLAMA_CURL=ON \
 && cmake --build build --config Release -j"$(nproc)" --target llama-server llama-cli rpc-server llama-bench

FROM nvcr.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends libcurl4 libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bin/ /usr/local/bin/
COPY --from=build /src/build/ggml/src/*.so /src/build/src/*.so /usr/local/lib/
RUN ldconfig
ENTRYPOINT ["llama-server"]
