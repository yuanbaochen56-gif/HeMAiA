# Two GEMMs with a fault-triggered shallow exit.
MK_DIR := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
HOST_APP_TYPE = offload_bingo_hw
CHIP_TYPE = single_chip
WORKLOAD = early_exit_2cluster
DEV_APP = snax-bingo-offload
SRCS = ../../src/offload_bingo_hw.c $(MK_DIR)/requant.c
BINGO_HOST ?= 1
INCL_DEVICE_BINARY ?= 1

DATA_CFG ?= $(MK_DIR)/params.hjson
DATA_H = $(MK_DIR)/early_exit_data.h
OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h
BENDER = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)
PLATFORM_H = $(MK_DIR)/../../../../../../../../target/sw/shared/platform/generated/occamy.h

$(DATA_H) $(OFFLOAD_H): $(MK_DIR)/main_bingo.py $(MK_DIR)/early_exit_datagen.py $(MK_DIR)/requant.h $(DATA_CFG) $(PLATFORM_H)
	python3 $(MK_DIR)/main_bingo.py \
		--output_dir $(MK_DIR) --data_h $(DATA_H) -c $(DATA_CFG) \
		--hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_cluster.hjson \
		--platformcfg $(PLATFORM_H)

INCDIRS += $(MK_DIR)
PARTIAL_OUTPUTS += $(DATA_H)
PARTIAL_OUTPUTS += $(OFFLOAD_H)
include ../../../../common.mk
