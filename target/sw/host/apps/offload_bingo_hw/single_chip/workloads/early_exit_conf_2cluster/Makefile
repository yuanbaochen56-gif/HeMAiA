# Data-driven and fault-triggered shallow exits share the same CERF path.
MK_DIR := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
B3_DIR := $(MK_DIR)/../early_exit_2cluster
HOST_APP_TYPE = offload_bingo_hw
CHIP_TYPE = single_chip
WORKLOAD = early_exit_conf_2cluster
DEV_APP = snax-bingo-offload
SRCS = ../../src/offload_bingo_hw.c $(B3_DIR)/requant.c $(MK_DIR)/ee_conf.c
BINGO_HOST ?= 1
INCL_DEVICE_BINARY ?= 1

DATA_CFG ?= $(MK_DIR)/params.hjson
DATA_H = $(MK_DIR)/early_exit_conf_data.h
MARGIN_JSON = $(MK_DIR)/early_exit_conf_data.json
OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h
BENDER = bender
SNITCH_ROOT = $(shell $(BENDER) path snitch_cluster)
PLATFORM_H = $(MK_DIR)/../../../../../../../../target/sw/shared/platform/generated/occamy.h

$(DATA_H) $(OFFLOAD_H) $(MARGIN_JSON): $(MK_DIR)/main_bingo.py $(MK_DIR)/early_exit_conf_datagen.py $(MK_DIR)/ee_conf.h $(B3_DIR)/early_exit_datagen.py $(B3_DIR)/main_bingo.py $(B3_DIR)/requant.h $(DATA_CFG) $(PLATFORM_H)
	python3 $(MK_DIR)/main_bingo.py \
		--output_dir $(MK_DIR) --data_h $(DATA_H) -c $(DATA_CFG) \
		--hwcfg $(SNITCH_ROOT)/target/snitch_cluster/cfg/snax_versacore_to_cluster.hjson \
		--platformcfg $(PLATFORM_H)

INCDIRS += $(MK_DIR) $(B3_DIR)
PARTIAL_OUTPUTS += $(DATA_H)
PARTIAL_OUTPUTS += $(OFFLOAD_H)
PARTIAL_OUTPUTS += $(MARGIN_JSON)
include ../../../../common.mk
