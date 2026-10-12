// Copyright 2026 KU Leuven.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51

// Testbench of hemaia_cdc_check on a cdc_fifo_gray, bound as in occamy_soc.
// +mode selects the reset applied after some traffic:
//   both      both sides enter reset together, the destination leaves it later
//             (the accelerator reset): the check stays silent and the items
//             sent afterwards arrive in order
//   src_only  only the source side is reset
//   dst_only  only the destination side is reset
// In the last two the FIFO pointers disagree afterwards and the destination
// pops items nobody pushed; the check must report them.

`timescale 1ns/1ps

module tb_hemaia_cdc_check;
  logic src_clk, dst_clk, src_rst_n, dst_rst_n;
  logic [7:0] src_data, dst_data;
  logic src_valid, src_ready, dst_valid, dst_ready;

  cdc_fifo_gray #(
    .WIDTH (8), .LOG_DEPTH (3)
  ) i_dut (
    .src_rst_ni (src_rst_n), .src_clk_i (src_clk),
    .src_data_i (src_data), .src_valid_i (src_valid), .src_ready_o (src_ready),
    .dst_rst_ni (dst_rst_n), .dst_clk_i (dst_clk),
    .dst_data_o (dst_data), .dst_valid_o (dst_valid), .dst_ready_i (dst_ready)
  );

  bind cdc_fifo_gray hemaia_cdc_check #(.NumCh(1)) i_acc_reset_cdc_check (
    .clk_a_i (src_clk_i), .rst_a_ni (src_rst_ni), .push_a_i (src_valid_i & src_ready_o),
    .clk_b_i (dst_clk_i), .rst_b_ni (dst_rst_ni), .pop_b_i (dst_valid_o & dst_ready_i)
  );

  // Host clock 28 ns, cluster clock at PM level 6 (12 ns)
  initial src_clk = 0;
  always #14 src_clk = ~src_clk;
  initial dst_clk = 0;
  always #6 dst_clk = ~dst_clk;

  // Scoreboard: every pop is the oldest item pushed since the last reset
  logic [7:0] expected [$];
  int pops = 0, wrong = 0, unexpected = 0;
  always @(posedge src_clk) if (src_rst_n && src_valid && src_ready) expected.push_back(src_data);
  always @(posedge dst_clk) begin
    if (dst_rst_n && dst_valid && dst_ready) begin
      pops++;
      if (expected.size() == 0) unexpected++;
      else if (expected.pop_front() !== dst_data) wrong++;
    end
  end

  task automatic send(input int unsigned count);
    repeat (count) begin
      src_data = $urandom();
      src_valid = 1'b1;
      do @(posedge src_clk); while (!src_ready);
      #1 src_valid = 1'b0;
    end
  endtask

  string mode;
  initial begin
    if (!$value$plusargs("mode=%s", mode)) mode = "both";
    src_valid = 0; dst_ready = 1;
    src_rst_n = 0; dst_rst_n = 0;
    repeat (4) @(posedge src_clk);
    #1 src_rst_n = 1;
    @(posedge dst_clk);
    #1 dst_rst_n = 1;
    send(5);
    repeat (20) @(posedge src_clk);
    // Reset with both pointers at 5 and the FIFO empty
    #1;
    if (mode != "dst_only") src_rst_n = 0;
    if (mode != "src_only") dst_rst_n = 0;
    expected.delete();
    repeat (8) @(posedge src_clk);
    #1 src_rst_n = 1;
    repeat (2) @(posedge dst_clk);
    #1 dst_rst_n = 1;
    repeat (40) @(posedge src_clk);
    send(7);
    repeat (40) @(posedge src_clk);
    $display("[TB_CDC] mode=%s pops=%0d unexpected=%0d wrong=%0d left=%0d",
             mode, pops, unexpected, wrong, expected.size());
    if (unexpected == 0 && wrong == 0 && expected.size() == 0 && pops == 12)
      $display("[TB_CDC] CLEAN");
    else
      $display("[TB_CDC] SPURIOUS");
    $finish;
  end
endmodule
