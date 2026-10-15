# Copyright 2025 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Scheduling micro-benchmark on the software runtime: the graph of
# offload_bingo_hw/single_chip/workloads/sched_chain_2cluster, scheduled by the
# host (bingo_runtime_schedule).
#   -DBINGO_SCHED_N=<copies>   (default 4)
#   -DBINGO_SCHED_PAR=1        independent copies instead of a chain
MK_DIR   := $(abspath $(dir $(realpath $(lastword $(MAKEFILE_LIST)))))

HOST_APP_TYPE = offload_bingo_sw
CHIP_TYPE     = single_chip
WORKLOAD      = sched_chain_2cluster
DEV_APP       = snax-bingo-offload

SRCS = ../../src/offload_bingo_sw.c

BINGO_HOST ?= 1 # True to include the bingo runtime
INCL_DEVICE_BINARY ?= 1 # True to include the device binary
###############
# Build settings
###############
INCDIRS += $(MK_DIR)

include ../../../../common.mk
