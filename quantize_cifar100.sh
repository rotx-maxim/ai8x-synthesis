#!/bin/sh
./quantize.py trained/ai85-cifar100-noqat.pth.tar trained/ai85-cifar100-noqat-q.pth.tar --device MAX78000 -v -c networks/cifar100-simple.yaml --scale 1.0 "$@"
