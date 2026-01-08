#!/bin/bash
python -m grpc_tools.protoc -I./protos --python_out=./generated --pyi_out=./generated ./protos/config.proto
