// Copyright 2020 ETH Zurich and University of Bologna.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51
// Licensed under Solderpad Hardware License, Version 0.51, see LICENSE for details.
{
   param_list: [
    { name: "NumClusters",
      desc: "Number of clusters in the chip.",
      type: "int",
      default: "${nr_clusters}"
    },
    { name: "NumTotalCores",
      desc: "Number of cores in the chip",
      type: "int",
      default: "${bingo_hw_manager_nr_cores_per_chiplet}"
    },    
  ], 
  name: "occamy_quad_periph",
  clock_primary: "clk_i",
  bus_interfaces: [
    { protocol: "reg_iface", direction: "device" }
  ],
  regwidth: 32,
  registers: [
    { multireg:
      { name: "ARG_PTR_LIST_BASE_ADDR",
        desc: "The base address of the arg ptrs.",
        swaccess: "rw",
        hwaccess: "none",
        count: "NumClusters",
        cname: "arg_ptr_list_base_addr",
        fields: [
          {
            bits: "31:0",
            resval: "0",
            name: "ARG_PTR_LIST_BASE_ADDR",
            desc: '''
                  The base address of the arg ptrs.
                  '''
          }
        ]
      }
    },
    { multireg:
      { name: "KERNEL_PTR_LIST_BASE_ADDR",
        desc: "The base address of the kernel ptrs.",
        swaccess: "rw",
        hwaccess: "none",
        count: "NumClusters",
        cname: "kernel_ptr_list_base_addr",
        fields: [
          {
            bits: "31:0",
            resval: "0",
            name: "KERNEL_PTR_LIST_BASE_ADDR",
            desc: '''
                  The base address of the kernel ptrs.
                  '''
          }
        ]
      }
    },
    { multireg:
      { name: "GLOBAL_TASK_ID_TO_DEV_TASK_ID_BASE_ADDR",
        desc: "The base address of the global task id to device task id mapping.",
        swaccess: "rw",
        hwaccess: "none",
        count: "NumClusters",
        cname: "global_task_id_to_dev_task_id_base_addr",
        fields: [
          {
            bits: "31:0",
            resval: "0",
            name: "GLOBAL_TASK_ID_TO_DEV_TASK_ID_BASE_ADDR",
            desc: '''
                  The base address of the global task id to device task id mapping.
                  '''
          }
        ]
      }
    },
    { name: "TASK_DESC_LIST_BASE_ADDR_HI",
      desc: "The higher 32bit base address of the task desciptor lists.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "TASK_DESC_LIST_BASE_ADDR_HI",
          desc: '''
                The higher 32bit base address of the task descriptor lists.
                '''
        }
      ]
    },
    { name: "TASK_DESC_LIST_BASE_ADDR_LO",
      desc: "The lower 32bit base address of the task desciptor lists.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "TASK_DESC_LIST_BASE_ADDR_LO",
          desc: '''
                The lower 32bit base address of the task descriptor lists.
                '''
        }
      ]
    },
    { name: "NUM_TASK",
      desc: "The number of task.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "NUM_TASK",
          desc: '''
                The number of tasks.
                '''
        }
      ]
    },
    { name: "START_BINGO_HW_MANAGER",
      desc: "Start the BINGO HW Manager",
      swaccess: "rw",
      hwaccess: "hrw",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "START_BINGO_HW_MANAGER",
          desc: '''
                Start the BINGO HW Manager.
                '''
        }
      ]
    },
    { name: "HOST_INIT_DONE",
      desc: "Host initialization done",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "HOST_INIT_DONE",
          desc: '''
                Host initialization done.
                '''
        }
      ]
    },
    { name: "EN_IDLE_PM",
      desc: "Enable the idle power optimization",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "EN_IDLE_PM",
          desc: '''
                Enable the idle power optimization of the BINGO HW Scheduler.
                '''
        }
      ]
    },
    { name: "IDLE_POWER_LEVEL",
      desc: "Core power level when at idle",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "IDLE_POWER_LEVEL",
          desc: '''
                The power level when the core is idle
                '''
        }
      ]
    },
    { name: "NORM_POWER_LEVEL",
      desc: "Core power level when at normal",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "NORM_POWER_LEVEL",
          desc: '''
                The power level when the core is running
                '''
        }
      ]
    },
    { name: "PM_BASE_ADDR_HI",
      desc: "The higher 32bit base address of the power manager.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "PM_BASE_ADDR_HI",
          desc: '''
                The higher 32bit base address of the power manager.
                '''
        }
      ]
    },
    { name: "PM_BASE_ADDR_LO",
      desc: "The lower 32bit base address of the power manager.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "PM_BASE_ADDR_LO",
          desc: '''
                The lower 32bit base address of the power manager.
                '''
        }
      ]
    },
    { multireg:
      { name: "CORE_POWER_DOMAIN",
        desc: "The power domains for each core.",
        swaccess: "rw",
        hwaccess: "hro",
        count: "NumTotalCores",
        cname: "core_power_domain",
        fields: [
          {
            bits: "31:0",
            resval: "0",
            name: "CORE_POWER_DOMAIN",
            desc: '''
                  The power domains for each core
                  '''
          }
        ]
      }
    },
    // DARTS Tier 1: CERF (Conditional Execution Register File) CSRs
    { name: "CERF_STATE",
      desc: "DARTS CERF: 32-bit bitmask of active groups (bit[i] = group i)",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "CERF_STATE",
          desc: '''Bit i = 1 means group i is active'''
        }
      ]
    },
    { name: "CERF_WRITE_EN",
      desc: "DARTS CERF: write enable (write 1 to latch CERF_STATE into HW, auto-clears)",
      swaccess: "rw",
      hwaccess: "hrw",
      fields: [
        {
          bits: "0",
          resval: "0",
          name: "CERF_WRITE_EN",
          desc: '''Write 1 to trigger CERF latch; HW clears after one cycle'''
        }
      ]
    },
    { name: "CERF_STATUS",
      desc: "DARTS CERF: read-back of actual HW CERF state (read-only)",
      swaccess: "ro",
      hwaccess: "hwo",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "CERF_STATUS",
          desc: '''Actual CERF bitmask from HW controller (read-only). Use this for read-modify-write.'''
        }
      ]
    },
    // DVFS: mode select + host<->PM doorbell handshake registers
    { name: "PM_MODE",
      desc: "Power management mode: 0 = DFS (PM scales clock), 1 = DVFS (PM notifies host)",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "PM_MODE",
          desc: '''0 = DFS (autonomous clock scaling), 1 = DVFS (notify host to scale V+F)'''
        }
      ]
    },
    { name: "DVFS_CLINT_MSIP_ADDR_HI",
      desc: "Higher 32bit of the CLINT MSIP word address used for the DVFS doorbell.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "DVFS_CLINT_MSIP_ADDR_HI",
          desc: '''Higher 32bit of the CLINT MSIP word address (host programs {chip_id, 0x0400_0000}).'''
        }
      ]
    },
    { name: "DVFS_CLINT_MSIP_ADDR_LO",
      desc: "Lower 32bit of the CLINT MSIP word address used for the DVFS doorbell.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "DVFS_CLINT_MSIP_ADDR_LO",
          desc: '''Lower 32bit of the CLINT MSIP word address.'''
        }
      ]
    },
    { name: "DVFS_REQUEST",
      desc: "DVFS request published by the PM for the host ISR (read-only).",
      swaccess: "ro",
      hwaccess: "hwo",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "DVFS_REQUEST",
          desc: '''bit0 = pending, bit1 = direction (1=raise/0=lower), bits[15:8] = target level'''
        }
      ]
    },
    { name: "DVFS_ACK",
      desc: "Host acknowledge: the power level the host has applied (closes the DVFS handshake).",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        {
          bits: "31:0",
          resval: "0",
          name: "DVFS_ACK",
          desc: '''Host writes the applied target level here after driving PMIC + clk/rst.'''
        }
      ]
    },
    // Bingo HW manager watchdog / replay / level-3 remote link status
    { name: "BINGO_STATUS",
      desc: "Bingo HW manager status (read-only, live; the sticky bits clear only on reset).",
      swaccess: "ro",
      hwaccess: "hwo",
      fields: [
        { bits: "0", resval: "0", name: "REPLAY_STUCK",
          desc: '''A fenced core holds a task that no live core may run (replay_stuck_o), on this chiplet or, for a level-3 export, on the chiplet that rejected it.'''
        },
        { bits: "1", resval: "0", name: "REMOTE_DONE_MISMATCH",
          desc: '''Sticky: a remote done did not belong to the exported head task of its proxy slot (remote_done_mismatch_o).'''
        },
        { bits: "7:2", resval: "0", name: "REMOTE_LINK_ERROR",
          desc: '''Sticky bingo_hw_manager_remote_link error_o: [0] SLVERR on a sent packet (resent), [1] unknown packet kind, [2] sequence error, [3] packet from an unknown peer, [4] credit overflow, [5] a packet dropped after its last resend (lost).'''
        },
        { bits: "8", resval: "0", name: "REMOTE_TIMEOUT",
          desc: '''Sticky: a proxy slot gave up waiting for the remote done of an exported task (REMOTE_PROXY_TIMEOUT) and stopped as on a reject (remote_timeout_o).'''
        }
      ]
    },
    { name: "BINGO_CORE_DEAD_SUSPECT",
      desc: "Bingo watchdog: busy cores without heartbeat (bit core + cluster * cores per cluster, host slot included).",
      swaccess: "ro",
      hwaccess: "hwo",
      fields: [
        { bits: "31:0", resval: "0", name: "BINGO_CORE_DEAD_SUSPECT",
          desc: '''Bit i: bingo slot i is dead_suspect (not sticky).'''
        }
      ]
    },
    { name: "BOOST_POWER_LEVEL",
      desc: "Bingo recovery boost: power level (clock divider) of a domain whose core runs a dead core's tasks; 0 = off",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        { bits: "31:0", resval: "0", name: "BOOST_POWER_LEVEL",
          desc: '''Power level while a substitute of a fenced core is busy (0: no boost).'''
        }
      ]
    },
    { name: "IDLE_ENTRY_DELAY",
      desc: "Bingo control plane: cycles a core must be idle before its power domain may drop to the idle level; 0 = at once",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        { bits: "31:0", resval: "0", name: "IDLE_ENTRY_DELAY",
          desc: '''Idle entry delay in quad_ctrl cycles (0: the domain drops as soon as all its cores are idle).'''
        }
      ]
    },
    { name: "REMOTE_PROXY_TIMEOUT",
      desc: "Bingo level 3: cycles a proxy slot waits for the remote done of its exported head before it gives up (stops, REMOTE_TIMEOUT); 0 = wait forever",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        { bits: "31:0", resval: "0", name: "REMOTE_PROXY_TIMEOUT",
          desc: '''Proxy timeout in quad_ctrl cycles (0: never).'''
        }
      ]
    },
    { name: "ACCESS_WAKE_HOLD",
      desc: "Bingo control plane: a cluster accessed from outside (e.g. the host reading its L1) keeps its power domain awake this many cycles after the last access; 0 = off",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        { bits: "31:0", resval: "0", name: "ACCESS_WAKE_HOLD",
          desc: '''Access wake hold in quad_ctrl cycles (0: accesses do not affect the power level).'''
        }
      ]
    },
    { name: "BINGO_PARK_REQ",
      desc: "Bingo core parking request (bit core + cluster * cores per cluster, host slot included): the slot drains, then its later tasks run on a live core of its type; 0 = no change.",
      swaccess: "rw",
      hwaccess: "hro",
      fields: [
        { bits: "31:0", resval: "0", name: "BINGO_PARK_REQ",
          desc: '''Bit i: park bingo slot i (level, see bingo_hw_manager_ctrl). Clearing it does not move the tasks back.'''
        }
      ]
    },
    { name: "BINGO_PARK_FAIL",
      desc: "Bingo core parking failed (same bits as BINGO_PARK_REQ): no live core of the type, the slot runs another slot's tasks, or its substitute died with none left; stays set until the request bit is cleared.",
      swaccess: "ro",
      hwaccess: "hwo",
      fields: [
        { bits: "31:0", resval: "0", name: "BINGO_PARK_FAIL",
          desc: '''Bit i: the park of bingo slot i failed.'''
        }
      ]
    },
    { name: "BINGO_CORE_FENCED",
      desc: "Bingo watchdog: fenced (confirmed dead, sticky) cores (bit core + cluster * cores per cluster, host slot included).",
      swaccess: "ro",
      hwaccess: "hwo",
      fields: [
        { bits: "31:0", resval: "0", name: "BINGO_CORE_FENCED",
          desc: '''Bit i: bingo slot i is fenced.'''
        }
      ]
    },
  ]
}
