// Copyright 2026 KU Leuven.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51

// Accelerator reset: resets the quadrant (bingo HW manager included) and all of
// its clusters together, without resetting the host.
//
// A request is a low pulse on req_ni (one more channel of the clock and reset
// controller, written by the host). The sequence runs on the host clock:
//   1. ISOLATE: isolate the AXI ports between SoC and quadrant and wait until
//      every port reports that its outstanding transactions completed;
//   2. RESET:   hold the quadrant and every cluster in reset for ResetCycles;
//   3. RELEASE: release the resets and wait ReleaseCycles, then end the
//      isolation.
// Both sides of every quadrant-cluster CDC are in reset at the same time, and
// no AXI transaction crosses the SoC boundary while either side is in reset.
// A host access to the quadrant during the sequence waits at the isolation.

`include "common_cells/registers.svh"

module hemaia_acc_reset #(
  parameter int unsigned NumClusters = 1,
  // AXI ports between the SoC and the quadrant
  parameter int unsigned NumPorts = 4,
  // Host cycles the quadrant and the clusters stay in reset
  parameter int unsigned ResetCycles = 64,
  // Host cycles between the reset release and the end of the isolation
  parameter int unsigned ReleaseCycles = 64
) (
  // Host clock and reset
  input  logic                   clk_i,
  input  logic                   rst_ni,
  // Reset request, active low, asynchronous to clk_i
  input  logic                   req_ni,
  // Cluster clocks and resets from the clock and reset controller
  input  logic [NumClusters-1:0] clk_acc_i,
  input  logic [NumClusters-1:0] rst_acc_ni,
  // Quadrant and cluster resets
  output logic                   rst_quad_no,
  output logic [NumClusters-1:0] rst_acc_no,
  // Isolation of the SoC-quadrant AXI ports
  output logic                   isolate_o,
  input  logic [NumPorts-1:0]    isolated_i
);

  localparam int unsigned CntWidth = $clog2((ResetCycles > ReleaseCycles ? ResetCycles : ReleaseCycles) + 1);

  typedef enum logic [1:0] {Idle, Isolate, Reset, Release} state_e;
  state_e state_d, state_q;
  logic [CntWidth-1:0] cnt_d, cnt_q;
  logic req, req_q, start;
  logic quad_rst_d, quad_rst_q;

  // The request is a reset output of the controller: synchronize its active
  // phase and start on its rising edge. The synchronizer and req_q reset to 1,
  // so a request that is still active when the host leaves reset starts nothing.
  sync #(
    .STAGES     (2),
    .ResetValue (1'b1)
  ) i_sync_req (
    .clk_i,
    .rst_ni,
    .serial_i (~req_ni),
    .serial_o (req)
  );
  `FF(req_q, req, 1'b1, clk_i, rst_ni)
  assign start = req & ~req_q;

  always_comb begin
    state_d    = state_q;
    cnt_d      = cnt_q;
    quad_rst_d = 1'b0;
    isolate_o  = 1'b1;
    unique case (state_q)
      Idle: begin
        isolate_o = 1'b0;
        if (start) state_d = Isolate;
      end
      Isolate: begin
        if (&isolated_i) begin
          state_d    = Reset;
          cnt_d      = '0;
          quad_rst_d = 1'b1;
        end
      end
      Reset: begin
        quad_rst_d = 1'b1;
        cnt_d      = cnt_q + 1;
        if (cnt_q == CntWidth'(ResetCycles - 1)) begin
          state_d    = Release;
          cnt_d      = '0;
          quad_rst_d = 1'b0;
        end
      end
      Release: begin
        cnt_d = cnt_q + 1;
        if (cnt_q == CntWidth'(ReleaseCycles - 1)) state_d = Idle;
      end
      default: state_d = Idle;
    endcase
  end

  `FF(state_q, state_d, Idle, clk_i, rst_ni)
  `FF(cnt_q, cnt_d, '0, clk_i, rst_ni)
  `FF(quad_rst_q, quad_rst_d, 1'b0, clk_i, rst_ni)

  // Quadrant: asserted with the host reset or from a register of the host clock
  assign rst_quad_no = rst_ni & ~quad_rst_q;

  // Clusters: asserted at once, released two cluster clock edges after the
  // quadrant. Without a request the hold flops shift in 0, so the cluster
  // resets of the controller pass through unchanged.
  for (genvar i = 0; i < NumClusters; i++) begin : gen_acc
    logic [1:0] hold_q;
    always_ff @(posedge clk_acc_i[i] or posedge quad_rst_q) begin
      if (quad_rst_q) hold_q <= 2'b11;
      else            hold_q <= {hold_q[0], 1'b0};
    end
    assign rst_acc_no[i] = rst_acc_ni[i] & ~hold_q[1];
  end

  // pragma translate_off
  `ifndef SYNTHESIS
  always @(posedge clk_i) begin
    if (rst_ni && start) $display("[ACC_RESET] %0t request", $time);
    if (rst_ni && state_q == Isolate && state_d == Reset) $display("[ACC_RESET] %0t reset", $time);
    if (rst_ni && state_q == Reset && state_d == Release) $display("[ACC_RESET] %0t release", $time);
    if (rst_ni && state_q == Release && state_d == Idle) $display("[ACC_RESET] %0t done", $time);
  end
  `endif
  `ifndef VERILATOR
  default disable iff (!rst_ni);
  // The quadrant enters reset only when every port is isolated
  a_reset_isolated: assert property (@(posedge clk_i) $rose(quad_rst_q) |-> $past(&isolated_i));
  // The isolation holds as long as the quadrant is in reset
  a_isolate_reset: assert property (@(posedge clk_i) quad_rst_q |-> isolate_o && (&isolated_i));
  `endif
  // pragma translate_on

endmodule
