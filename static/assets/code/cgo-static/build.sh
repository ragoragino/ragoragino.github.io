#!/bin/bash

set -e

go build -x -work -ldflags '-linkmode external -extldflags "-static"' .