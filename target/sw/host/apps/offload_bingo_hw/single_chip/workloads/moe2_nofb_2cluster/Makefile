# MoE2: top-1 with an ordered CERF fallback expert.
MK_DIR   := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
HOST_APP_TYPE = offload_bingo_hw
CHIP_TYPE     = single_chip
WORKLOAD      = moe2_nofb_2cluster
DEV_APP       = snax-bingo-offload
SRCS = ../../src/offload_bingo_hw.c
BINGO_HOST ?= 1
INCL_DEVICE_BINARY ?= 1

DATA_CFG ?= $(MK_DIR)/params.hjson
DATA_H = $(MK_DIR)/moe_data.h
OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h
BENDER   = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)
PLATFORM_H = $(MK_DIR)/../../../../../../../../target/sw/shared/platform/generated/occamy.h

$(DATA_H) $(OFFLOAD_H): $(MK_DIR)/../moe2_2cluster/main_bingo.py $(MK_DIR)/../moe2_2cluster/moe_datagen.py $(DATA_CFG) $(PLATFORM_H) $(MK_DIR)/../moe4_4cluster/moe_datagen.py
	python3 $(MK_DIR)/../moe2_2cluster/main_bingo.py \
		--output_dir $(MK_DIR) \
		--data_h $(DATA_H) \
		-c $(DATA_CFG) \
		--hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_cluster.hjson \
		--platformcfg $(PLATFORM_H)

INCDIRS += $(MK_DIR)
PARTIAL_OUTPUTS += $(DATA_H)
PARTIAL_OUTPUTS += $(OFFLOAD_H)

include ../../../../common.mk
