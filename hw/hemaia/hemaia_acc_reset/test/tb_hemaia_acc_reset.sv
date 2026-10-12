// Copyright 2026 KU Leuven.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51

// Testbench of hemaia_acc_reset. The SoC-quadrant ports are modelled as
// random traffic with outstanding counters that obey the isolation like
// axi_isolate (no new transaction while isolated, isolated once drained).
// Checks, on every host clock edge:
//   K1 the quadrant is in reset only with every port isolated and drained
//   K2 every cluster is in reset while the quadrant is
//   K3 no cluster leaves reset while the quadrant is still in reset
//   K4 the quadrant stays in reset exactly ResetCycles host cycles
//   K5 one sequence per request, none without a request (boot included)
//   K6 without a sequence the resets pass through unchanged
//   K7 the isolation ends ReleaseCycles host cycles after the reset release
//   K8 a port that never drains blocks the reset; once it drains, it runs

`timescale 1ns/1ps

module tb_hemaia_acc_reset;
  localparam int unsigned NC = 2;
  localparam int unsigned NP = 4;
  localparam int unsigned RC = 64;
  localparam int unsigned RL = 64;

  logic clk, rst_n, req_n;
  logic [NC-1:0] clk_acc, rst_acc_n;
  logic rst_quad_n;
  logic [NC-1:0] rst_acc_out_n;
  logic isolate;
  logic [NP-1:0] isolated;

  hemaia_acc_reset #(
    .NumClusters (NC), .NumPorts (NP), .ResetCycles (RC), .ReleaseCycles (RL)
  ) i_dut (
    .clk_i (clk), .rst_ni (rst_n), .req_ni (req_n),
    .clk_acc_i (clk_acc), .rst_acc_ni (rst_acc_n),
    .rst_quad_no (rst_quad_n), .rst_acc_no (rst_acc_out_n),
    .isolate_o (isolate), .isolated_i (isolated)
  );

  // Host clock 28 ns; cluster clocks at the PM levels 6 and 25 (2 ns master)
  initial clk = 0;
  always #14 clk = ~clk;
  real acc_half [NC] = '{6.0, 25.0};
  for (genvar i = 0; i < NC; i++) begin : gen_clk
    initial clk_acc[i] = 0;
    always #(acc_half[i]) clk_acc[i] = ~clk_acc[i];
  end

  // Port model
  int unsigned outstanding [NP];
  bit drain_en [NP];
  bit iso_q [NP];
  int errors = 0;
  for (genvar p = 0; p < NP; p++) begin : gen_port
    assign isolated[p] = iso_q[p];
    always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
        outstanding[p] <= 0;
        iso_q[p] <= 1'b0;
      end else begin
        automatic int unsigned next = outstanding[p];
        if (!isolate && !iso_q[p] && $urandom_range(3) == 0 && next < 16) begin
          if (!rst_quad_n) begin
            $error("K1 port %0d starts a transaction while the quadrant is in reset", p);
            errors++;
          end
          next++;
        end
        if (drain_en[p] && next > 0 && $urandom_range(2) == 0) next--;
        outstanding[p] <= next;
        iso_q[p] <= isolate && (outstanding[p] == 0) && (iso_q[p] || next == 0);
      end
    end
  end

  // Reference: count requests, sequences and check the invariants
  int requests = 0, sequences = 0, quad_low = 0, since_release = -1;
  always @(posedge clk) begin
    if (rst_n) begin
      if (!rst_quad_n) begin
        if (!isolate || !(&isolated)) begin
          $error("K1 quadrant in reset without full isolation");
          errors++;
        end
        for (int p = 0; p < NP; p++) if (outstanding[p] != 0) begin
          $error("K1 port %0d has %0d outstanding transactions during the reset", p, outstanding[p]);
          errors++;
        end
        if (rst_acc_out_n != '0) begin
          $error("K2 cluster out of reset while the quadrant is in reset: %b", rst_acc_out_n);
          errors++;
        end
        quad_low++;
      end else if (quad_low != 0) begin
        if (quad_low != RC) begin
          $error("K4 quadrant reset lasted %0d host cycles, expected %0d", quad_low, RC);
          errors++;
        end
        sequences++;
        quad_low = 0;
        since_release = 0;
      end
      if (since_release >= 0) begin
        if (since_release < RL && !isolate) begin
          $error("K7 isolation ended %0d cycles after the reset release", since_release);
          errors++;
        end
        if (since_release >= RL + 2 && isolate) begin
          $error("K7 isolation still on %0d cycles after the reset release", since_release);
          errors++;
        end
        since_release++;
        if (since_release > RL + 2) since_release = -1;
      end
      if (!isolate && since_release < 0) begin
        if (rst_quad_n !== 1'b1 || rst_acc_out_n !== rst_acc_n) begin
          $error("K6 resets changed without a sequence: quad %b clusters %b (in %b)",
                 rst_quad_n, rst_acc_out_n, rst_acc_n);
          errors++;
        end
      end
    end else begin
      quad_low = 0;
      since_release = -1;
    end
  end

  // K3: a cluster leaves reset only after the quadrant did
  for (genvar i = 0; i < NC; i++) begin : gen_k3
    always @(posedge rst_acc_out_n[i]) begin
      if (rst_n && !rst_quad_n) begin
        $error("K3 cluster %0d left reset before the quadrant", i);
        errors++;
      end
    end
  end

  // Controller model: a request is a low pulse of the request channel
  task automatic request(input int unsigned cycles);
    @(posedge clk);
    #3 req_n = 1'b0;
    repeat (cycles) @(posedge clk);
    #3 req_n = 1'b1;
    requests++;
  endtask

  task automatic wait_idle();
    do @(posedge clk); while (isolate || !rst_quad_n || since_release >= 0);
    repeat (4) @(posedge clk);
  endtask

  initial begin
    for (int p = 0; p < NP; p++) drain_en[p] = 1;
    // Boot: every channel in reset; the request channel leaves reset after the host
    rst_n = 0; req_n = 0; rst_acc_n = '0;
    repeat (5) @(posedge clk);
    #3 rst_n = 1;
    repeat (3) @(posedge clk);
    #3 req_n = 1;
    repeat (2) @(posedge clk);
    #3 rst_acc_n = '1;
    repeat (200) @(posedge clk);
    if (sequences != 0) begin
      $error("K5 %0d sequences at boot", sequences);
      errors++;
    end
    // Requests with random traffic
    for (int r = 0; r < 6; r++) begin
      request(6);
      wait_idle();
      repeat ($urandom_range(50, 10)) @(posedge clk);
    end
    // A request held longer than the whole sequence
    request(3 * (RC + RL));
    wait_idle();
    // K8: one port never drains; the reset waits until it does
    drain_en[2] = 0;
    repeat (20) @(posedge clk);
    while (outstanding[2] == 0) @(posedge clk);
    request(6);
    repeat (400) @(posedge clk);
    if (!rst_quad_n || sequences != requests - 1) begin
      $error("K8 reset with a port that has not drained");
      errors++;
    end
    drain_en[2] = 1;
    wait_idle();
    // Host reset in the middle of a sequence ends it
    request(6);
    do @(posedge clk); while (rst_quad_n);
    repeat (10) @(posedge clk);
    #3 rst_n = 0; rst_acc_n = '0;
    repeat (3) @(posedge clk);
    #3 rst_n = 1;
    repeat (8) @(posedge clk);
    #3 rst_acc_n = '1;
    requests--;  // the interrupted sequence is not counted
    repeat (100) @(posedge clk);
    if (isolate || !rst_quad_n) begin
      $error("host reset did not end the sequence");
      errors++;
    end
    request(6);
    wait_idle();
    if (sequences != requests) begin
      $error("K5 %0d requests, %0d sequences", requests, sequences);
      errors++;
    end
    $display("[TB_ACC_RESET] requests=%0d sequences=%0d errors=%0d", requests, sequences, errors);
    if (errors == 0) $display("[TB_ACC_RESET] PASS");
    else $display("[TB_ACC_RESET] FAIL");
    $finish;
  end

  initial begin
    #5ms;
    $display("[TB_ACC_RESET] FAIL timeout");
    $finish;
  end
endmodule
