`include "common_cells/assertions.svh"
`include "register_interface/typedef.svh"

`timescale 1ps / 1fs
module hemaia_clk_rst_controller #(
    parameter int USE_VENDOR_PLL = 0, // Set to 1 to use the vendor PLL, 0 to bypass the PLL and use the input clock directly
    parameter int NumClocks = 4,
    parameter int MaxDivisionWidth = 8,  // Maximum width for clock division
    parameter int DefaultDivision[NumClocks] = '{default: 16},
    parameter int ResetDelays[NumClocks] = '{default: 1},
    parameter type axi_lite_req_t = logic,
    parameter type axi_lite_rsp_t = logic
) (
    // Test mode
    (* false_path *) input logic test_mode_i,
    // The clock and rst for the controller itself
    input logic control_clk_i,
    (* false_path *) input logic control_rst_ni,
    // Data bus for controller
    input axi_lite_req_t axi_lite_req_i,
    output axi_lite_rsp_t axi_lite_rsp_o,

    // Input / Output clk, reset
    input logic mst_clk_i,
    input logic mst_rst_ni,
    output logic clk_obs_o,
    output logic [NumClocks-1:0] clk_o,
    output logic [NumClocks-1:0] rst_no,
    // PLL control signals
    input logic pll_bypass_i,
    input logic pll_en_i,
    input logic [1:0] pll_post_div_sel_i,
    output logic pll_lock_o
);
  import hemaia_clk_rst_controller_reg_pkg::*;
  hemaia_clk_rst_controller_reg2hw_t reg2hw;
  hemaia_clk_rst_controller_hw2reg_t hw2reg;

  //////////////////////////////////////
  //    PLL (Not implemented yet)     //
  //////////////////////////////////////

  logic mst_clk_after_pll;
  logic pll_test_en;
  logic [2:0] pll_test_sel;
  logic pll_test_out;
  assign pll_test_en = reg2hw.pll_test_en_register[0];
  assign pll_test_sel = reg2hw.pll_test_sel_register[2:0];
  assign hw2reg.pll_test_out_register[0] = pll_test_out;
  hemaia_pll_wrapper #(
      .USE_VENDOR_PLL(USE_VENDOR_PLL)
  ) i_pll (
      .clk_i(mst_clk_i),
      .clk_o(mst_clk_after_pll),
      .pad_pll_bypass_i(pll_bypass_i),
      .pad_pll_en_i(pll_en_i),
      .pad_pll_post_div_sel_i(pll_post_div_sel_i),
      .pad_pll_lock_o(pll_lock_o),
      .pll_test_en_i(pll_test_en),
      .pll_test_sel_i(pll_test_sel),
      .pll_test_out_o(pll_test_out)
  );

  ///////////////////////////////
  //    Reset Synchronizer     //
  ///////////////////////////////
  logic mst_rst_n_d1_mst_clk, mst_rst_n_d2_mst_clk;
  always_ff @(posedge mst_clk_after_pll or negedge mst_rst_ni) begin
    if (~mst_rst_ni) begin
      mst_rst_n_d1_mst_clk <= 1'b0;
      mst_rst_n_d2_mst_clk <= 1'b0;
    end else begin
      mst_rst_n_d1_mst_clk <= mst_rst_ni;
      mst_rst_n_d2_mst_clk <= mst_rst_n_d1_mst_clk;
    end
  end

  ///////////////////////////////////////////
  //    Reset Pipeline for Clock Dividers  //
  ///////////////////////////////////////////
  // Pipeline stages for reset distribution to clock dividers.
  // These registers will be replicated by DC (set_register_replication)
  // and placed near each clock divider, minimizing CTS skew for recovery checks.
  // Async reset uses mst_rst_ni directly (false-pathed in SDC).
  logic mst_rst_pipe_d1, mst_rst_pipe_d2;
  always_ff @(posedge mst_clk_after_pll or negedge mst_rst_ni) begin
    if (~mst_rst_ni) begin
      mst_rst_pipe_d1 <= 1'b0;
      mst_rst_pipe_d2 <= 1'b0;
    end else begin
      mst_rst_pipe_d1 <= mst_rst_n_d2_mst_clk;
      mst_rst_pipe_d2 <= mst_rst_pipe_d1;
    end
  end

  ///////////////////////
  //    CONTROLLER     //
  ///////////////////////


  `REG_BUS_TYPEDEF_ALL(reg_a48_d32, logic [47:0], logic [31:0], logic [3:0])

  reg_a48_d32_req_t controller_req;
  reg_a48_d32_rsp_t controller_rsp;

  axi_lite_to_reg #(
      .ADDR_WIDTH    (48),
      .DATA_WIDTH    (32),
      .axi_lite_req_t(axi_lite_req_t),
      .axi_lite_rsp_t(axi_lite_rsp_t),
      .reg_req_t     (reg_a48_d32_req_t),
      .reg_rsp_t     (reg_a48_d32_rsp_t)
  ) i_controller_axi_to_reg (
      .clk_i         (control_clk_i),
      .rst_ni        (control_rst_ni),
      .axi_lite_req_i(axi_lite_req_i),
      .axi_lite_rsp_o(axi_lite_rsp_o),
      .reg_req_o     (controller_req),
      .reg_rsp_i     (controller_rsp)
  );

  hemaia_clk_rst_controller_reg_top #(
      .reg_req_t(reg_a48_d32_req_t),
      .reg_rsp_t(reg_a48_d32_rsp_t)
  ) i_controller_reg_to_hw (
      .clk_i    (control_clk_i),
      .rst_ni   (control_rst_ni),
      .reg_req_i(controller_req),
      .reg_rsp_o(controller_rsp),
      .reg2hw   (reg2hw),
      .hw2reg   (hw2reg),
      .devmode_i('0)
  );

  ///////////////////////////////////////////////////////////////
  //    Clock division valid registers and the loaded division  //
  ///////////////////////////////////////////////////////////////
  // Two ways to program a divider, each with its own valid register:
  //  - CLOCK_VALID_REGISTER bit i loads divider i from its byte of the packed
  //    CLOCK_DIVISION_REGISTER_* (four domains per register),
  //  - DOMAIN_DIVISION_VALID bit i loads it from DOMAIN_DIVISION_C<i> (one
  //    register per domain).
  // A bit written 1 reads 1 for the cycle after the write and is cleared by hw
  // (the spike the dividers synchronise). In the cycle it is written, the
  // division of that domain is taken from the register set of that valid bit
  // into division_q, which the divider reads when it loads: later writes to
  // any division register do not change a pending load. So two masters that
  // program different domains, each with its own set (e.g. the host the packed
  // registers, the bingo power manager the per-domain ones), never need to
  // read-modify-write a word they share, and neither changes the other's clocks.
  logic [31:0] valid_set, domain_valid_set;   // bits written 1 in this cycle
  logic [31:0] valid_q, domain_valid_q;       // the spikes
  logic [31:0][7:0] domain_division;          // DOMAIN_DIVISION_C<i>
  logic [31:0][7:0] division_q;               // what each divider loads
  logic [31:0][7:0] clock_division_reg_concat;  // the packed registers, by domain
  assign valid_set[0] = reg2hw.clock_valid_register.valid_c0.qe & reg2hw.clock_valid_register.valid_c0.q;
  assign valid_set[1] = reg2hw.clock_valid_register.valid_c1.qe & reg2hw.clock_valid_register.valid_c1.q;
  assign valid_set[2] = reg2hw.clock_valid_register.valid_c2.qe & reg2hw.clock_valid_register.valid_c2.q;
  assign valid_set[3] = reg2hw.clock_valid_register.valid_c3.qe & reg2hw.clock_valid_register.valid_c3.q;
  assign valid_set[4] = reg2hw.clock_valid_register.valid_c4.qe & reg2hw.clock_valid_register.valid_c4.q;
  assign valid_set[5] = reg2hw.clock_valid_register.valid_c5.qe & reg2hw.clock_valid_register.valid_c5.q;
  assign valid_set[6] = reg2hw.clock_valid_register.valid_c6.qe & reg2hw.clock_valid_register.valid_c6.q;
  assign valid_set[7] = reg2hw.clock_valid_register.valid_c7.qe & reg2hw.clock_valid_register.valid_c7.q;
  assign valid_set[8] = reg2hw.clock_valid_register.valid_c8.qe & reg2hw.clock_valid_register.valid_c8.q;
  assign valid_set[9] = reg2hw.clock_valid_register.valid_c9.qe & reg2hw.clock_valid_register.valid_c9.q;
  assign valid_set[10] = reg2hw.clock_valid_register.valid_c10.qe & reg2hw.clock_valid_register.valid_c10.q;
  assign valid_set[11] = reg2hw.clock_valid_register.valid_c11.qe & reg2hw.clock_valid_register.valid_c11.q;
  assign valid_set[12] = reg2hw.clock_valid_register.valid_c12.qe & reg2hw.clock_valid_register.valid_c12.q;
  assign valid_set[13] = reg2hw.clock_valid_register.valid_c13.qe & reg2hw.clock_valid_register.valid_c13.q;
  assign valid_set[14] = reg2hw.clock_valid_register.valid_c14.qe & reg2hw.clock_valid_register.valid_c14.q;
  assign valid_set[15] = reg2hw.clock_valid_register.valid_c15.qe & reg2hw.clock_valid_register.valid_c15.q;
  assign valid_set[16] = reg2hw.clock_valid_register.valid_c16.qe & reg2hw.clock_valid_register.valid_c16.q;
  assign valid_set[17] = reg2hw.clock_valid_register.valid_c17.qe & reg2hw.clock_valid_register.valid_c17.q;
  assign valid_set[18] = reg2hw.clock_valid_register.valid_c18.qe & reg2hw.clock_valid_register.valid_c18.q;
  assign valid_set[19] = reg2hw.clock_valid_register.valid_c19.qe & reg2hw.clock_valid_register.valid_c19.q;
  assign valid_set[20] = reg2hw.clock_valid_register.valid_c20.qe & reg2hw.clock_valid_register.valid_c20.q;
  assign valid_set[21] = reg2hw.clock_valid_register.valid_c21.qe & reg2hw.clock_valid_register.valid_c21.q;
  assign valid_set[22] = reg2hw.clock_valid_register.valid_c22.qe & reg2hw.clock_valid_register.valid_c22.q;
  assign valid_set[23] = reg2hw.clock_valid_register.valid_c23.qe & reg2hw.clock_valid_register.valid_c23.q;
  assign valid_set[24] = reg2hw.clock_valid_register.valid_c24.qe & reg2hw.clock_valid_register.valid_c24.q;
  assign valid_set[25] = reg2hw.clock_valid_register.valid_c25.qe & reg2hw.clock_valid_register.valid_c25.q;
  assign valid_set[26] = reg2hw.clock_valid_register.valid_c26.qe & reg2hw.clock_valid_register.valid_c26.q;
  assign valid_set[27] = reg2hw.clock_valid_register.valid_c27.qe & reg2hw.clock_valid_register.valid_c27.q;
  assign valid_set[28] = reg2hw.clock_valid_register.valid_c28.qe & reg2hw.clock_valid_register.valid_c28.q;
  assign valid_set[29] = reg2hw.clock_valid_register.valid_c29.qe & reg2hw.clock_valid_register.valid_c29.q;
  assign valid_set[30] = reg2hw.clock_valid_register.valid_c30.qe & reg2hw.clock_valid_register.valid_c30.q;
  assign valid_set[31] = reg2hw.clock_valid_register.valid_c31.qe & reg2hw.clock_valid_register.valid_c31.q;
  assign domain_valid_set[0] = reg2hw.domain_division_valid.valid_c0.qe & reg2hw.domain_division_valid.valid_c0.q;
  assign domain_valid_set[1] = reg2hw.domain_division_valid.valid_c1.qe & reg2hw.domain_division_valid.valid_c1.q;
  assign domain_valid_set[2] = reg2hw.domain_division_valid.valid_c2.qe & reg2hw.domain_division_valid.valid_c2.q;
  assign domain_valid_set[3] = reg2hw.domain_division_valid.valid_c3.qe & reg2hw.domain_division_valid.valid_c3.q;
  assign domain_valid_set[4] = reg2hw.domain_division_valid.valid_c4.qe & reg2hw.domain_division_valid.valid_c4.q;
  assign domain_valid_set[5] = reg2hw.domain_division_valid.valid_c5.qe & reg2hw.domain_division_valid.valid_c5.q;
  assign domain_valid_set[6] = reg2hw.domain_division_valid.valid_c6.qe & reg2hw.domain_division_valid.valid_c6.q;
  assign domain_valid_set[7] = reg2hw.domain_division_valid.valid_c7.qe & reg2hw.domain_division_valid.valid_c7.q;
  assign domain_valid_set[8] = reg2hw.domain_division_valid.valid_c8.qe & reg2hw.domain_division_valid.valid_c8.q;
  assign domain_valid_set[9] = reg2hw.domain_division_valid.valid_c9.qe & reg2hw.domain_division_valid.valid_c9.q;
  assign domain_valid_set[10] = reg2hw.domain_division_valid.valid_c10.qe & reg2hw.domain_division_valid.valid_c10.q;
  assign domain_valid_set[11] = reg2hw.domain_division_valid.valid_c11.qe & reg2hw.domain_division_valid.valid_c11.q;
  assign domain_valid_set[12] = reg2hw.domain_division_valid.valid_c12.qe & reg2hw.domain_division_valid.valid_c12.q;
  assign domain_valid_set[13] = reg2hw.domain_division_valid.valid_c13.qe & reg2hw.domain_division_valid.valid_c13.q;
  assign domain_valid_set[14] = reg2hw.domain_division_valid.valid_c14.qe & reg2hw.domain_division_valid.valid_c14.q;
  assign domain_valid_set[15] = reg2hw.domain_division_valid.valid_c15.qe & reg2hw.domain_division_valid.valid_c15.q;
  assign domain_valid_set[16] = reg2hw.domain_division_valid.valid_c16.qe & reg2hw.domain_division_valid.valid_c16.q;
  assign domain_valid_set[17] = reg2hw.domain_division_valid.valid_c17.qe & reg2hw.domain_division_valid.valid_c17.q;
  assign domain_valid_set[18] = reg2hw.domain_division_valid.valid_c18.qe & reg2hw.domain_division_valid.valid_c18.q;
  assign domain_valid_set[19] = reg2hw.domain_division_valid.valid_c19.qe & reg2hw.domain_division_valid.valid_c19.q;
  assign domain_valid_set[20] = reg2hw.domain_division_valid.valid_c20.qe & reg2hw.domain_division_valid.valid_c20.q;
  assign domain_valid_set[21] = reg2hw.domain_division_valid.valid_c21.qe & reg2hw.domain_division_valid.valid_c21.q;
  assign domain_valid_set[22] = reg2hw.domain_division_valid.valid_c22.qe & reg2hw.domain_division_valid.valid_c22.q;
  assign domain_valid_set[23] = reg2hw.domain_division_valid.valid_c23.qe & reg2hw.domain_division_valid.valid_c23.q;
  assign domain_valid_set[24] = reg2hw.domain_division_valid.valid_c24.qe & reg2hw.domain_division_valid.valid_c24.q;
  assign domain_valid_set[25] = reg2hw.domain_division_valid.valid_c25.qe & reg2hw.domain_division_valid.valid_c25.q;
  assign domain_valid_set[26] = reg2hw.domain_division_valid.valid_c26.qe & reg2hw.domain_division_valid.valid_c26.q;
  assign domain_valid_set[27] = reg2hw.domain_division_valid.valid_c27.qe & reg2hw.domain_division_valid.valid_c27.q;
  assign domain_valid_set[28] = reg2hw.domain_division_valid.valid_c28.qe & reg2hw.domain_division_valid.valid_c28.q;
  assign domain_valid_set[29] = reg2hw.domain_division_valid.valid_c29.qe & reg2hw.domain_division_valid.valid_c29.q;
  assign domain_valid_set[30] = reg2hw.domain_division_valid.valid_c30.qe & reg2hw.domain_division_valid.valid_c30.q;
  assign domain_valid_set[31] = reg2hw.domain_division_valid.valid_c31.qe & reg2hw.domain_division_valid.valid_c31.q;
  assign domain_division[0] = reg2hw.domain_division_c0.q;
  assign domain_division[1] = reg2hw.domain_division_c1.q;
  assign domain_division[2] = reg2hw.domain_division_c2.q;
  assign domain_division[3] = reg2hw.domain_division_c3.q;
  assign domain_division[4] = reg2hw.domain_division_c4.q;
  assign domain_division[5] = reg2hw.domain_division_c5.q;
  assign domain_division[6] = reg2hw.domain_division_c6.q;
  assign domain_division[7] = reg2hw.domain_division_c7.q;
  assign domain_division[8] = reg2hw.domain_division_c8.q;
  assign domain_division[9] = reg2hw.domain_division_c9.q;
  assign domain_division[10] = reg2hw.domain_division_c10.q;
  assign domain_division[11] = reg2hw.domain_division_c11.q;
  assign domain_division[12] = reg2hw.domain_division_c12.q;
  assign domain_division[13] = reg2hw.domain_division_c13.q;
  assign domain_division[14] = reg2hw.domain_division_c14.q;
  assign domain_division[15] = reg2hw.domain_division_c15.q;
  assign domain_division[16] = reg2hw.domain_division_c16.q;
  assign domain_division[17] = reg2hw.domain_division_c17.q;
  assign domain_division[18] = reg2hw.domain_division_c18.q;
  assign domain_division[19] = reg2hw.domain_division_c19.q;
  assign domain_division[20] = reg2hw.domain_division_c20.q;
  assign domain_division[21] = reg2hw.domain_division_c21.q;
  assign domain_division[22] = reg2hw.domain_division_c22.q;
  assign domain_division[23] = reg2hw.domain_division_c23.q;
  assign domain_division[24] = reg2hw.domain_division_c24.q;
  assign domain_division[25] = reg2hw.domain_division_c25.q;
  assign domain_division[26] = reg2hw.domain_division_c26.q;
  assign domain_division[27] = reg2hw.domain_division_c27.q;
  assign domain_division[28] = reg2hw.domain_division_c28.q;
  assign domain_division[29] = reg2hw.domain_division_c29.q;
  assign domain_division[30] = reg2hw.domain_division_c30.q;
  assign domain_division[31] = reg2hw.domain_division_c31.q;
  assign hw2reg.clock_valid_register.valid_c0.d = valid_q[0];
  assign hw2reg.clock_valid_register.valid_c1.d = valid_q[1];
  assign hw2reg.clock_valid_register.valid_c2.d = valid_q[2];
  assign hw2reg.clock_valid_register.valid_c3.d = valid_q[3];
  assign hw2reg.clock_valid_register.valid_c4.d = valid_q[4];
  assign hw2reg.clock_valid_register.valid_c5.d = valid_q[5];
  assign hw2reg.clock_valid_register.valid_c6.d = valid_q[6];
  assign hw2reg.clock_valid_register.valid_c7.d = valid_q[7];
  assign hw2reg.clock_valid_register.valid_c8.d = valid_q[8];
  assign hw2reg.clock_valid_register.valid_c9.d = valid_q[9];
  assign hw2reg.clock_valid_register.valid_c10.d = valid_q[10];
  assign hw2reg.clock_valid_register.valid_c11.d = valid_q[11];
  assign hw2reg.clock_valid_register.valid_c12.d = valid_q[12];
  assign hw2reg.clock_valid_register.valid_c13.d = valid_q[13];
  assign hw2reg.clock_valid_register.valid_c14.d = valid_q[14];
  assign hw2reg.clock_valid_register.valid_c15.d = valid_q[15];
  assign hw2reg.clock_valid_register.valid_c16.d = valid_q[16];
  assign hw2reg.clock_valid_register.valid_c17.d = valid_q[17];
  assign hw2reg.clock_valid_register.valid_c18.d = valid_q[18];
  assign hw2reg.clock_valid_register.valid_c19.d = valid_q[19];
  assign hw2reg.clock_valid_register.valid_c20.d = valid_q[20];
  assign hw2reg.clock_valid_register.valid_c21.d = valid_q[21];
  assign hw2reg.clock_valid_register.valid_c22.d = valid_q[22];
  assign hw2reg.clock_valid_register.valid_c23.d = valid_q[23];
  assign hw2reg.clock_valid_register.valid_c24.d = valid_q[24];
  assign hw2reg.clock_valid_register.valid_c25.d = valid_q[25];
  assign hw2reg.clock_valid_register.valid_c26.d = valid_q[26];
  assign hw2reg.clock_valid_register.valid_c27.d = valid_q[27];
  assign hw2reg.clock_valid_register.valid_c28.d = valid_q[28];
  assign hw2reg.clock_valid_register.valid_c29.d = valid_q[29];
  assign hw2reg.clock_valid_register.valid_c30.d = valid_q[30];
  assign hw2reg.clock_valid_register.valid_c31.d = valid_q[31];
  assign hw2reg.domain_division_valid.valid_c0.d = domain_valid_q[0];
  assign hw2reg.domain_division_valid.valid_c1.d = domain_valid_q[1];
  assign hw2reg.domain_division_valid.valid_c2.d = domain_valid_q[2];
  assign hw2reg.domain_division_valid.valid_c3.d = domain_valid_q[3];
  assign hw2reg.domain_division_valid.valid_c4.d = domain_valid_q[4];
  assign hw2reg.domain_division_valid.valid_c5.d = domain_valid_q[5];
  assign hw2reg.domain_division_valid.valid_c6.d = domain_valid_q[6];
  assign hw2reg.domain_division_valid.valid_c7.d = domain_valid_q[7];
  assign hw2reg.domain_division_valid.valid_c8.d = domain_valid_q[8];
  assign hw2reg.domain_division_valid.valid_c9.d = domain_valid_q[9];
  assign hw2reg.domain_division_valid.valid_c10.d = domain_valid_q[10];
  assign hw2reg.domain_division_valid.valid_c11.d = domain_valid_q[11];
  assign hw2reg.domain_division_valid.valid_c12.d = domain_valid_q[12];
  assign hw2reg.domain_division_valid.valid_c13.d = domain_valid_q[13];
  assign hw2reg.domain_division_valid.valid_c14.d = domain_valid_q[14];
  assign hw2reg.domain_division_valid.valid_c15.d = domain_valid_q[15];
  assign hw2reg.domain_division_valid.valid_c16.d = domain_valid_q[16];
  assign hw2reg.domain_division_valid.valid_c17.d = domain_valid_q[17];
  assign hw2reg.domain_division_valid.valid_c18.d = domain_valid_q[18];
  assign hw2reg.domain_division_valid.valid_c19.d = domain_valid_q[19];
  assign hw2reg.domain_division_valid.valid_c20.d = domain_valid_q[20];
  assign hw2reg.domain_division_valid.valid_c21.d = domain_valid_q[21];
  assign hw2reg.domain_division_valid.valid_c22.d = domain_valid_q[22];
  assign hw2reg.domain_division_valid.valid_c23.d = domain_valid_q[23];
  assign hw2reg.domain_division_valid.valid_c24.d = domain_valid_q[24];
  assign hw2reg.domain_division_valid.valid_c25.d = domain_valid_q[25];
  assign hw2reg.domain_division_valid.valid_c26.d = domain_valid_q[26];
  assign hw2reg.domain_division_valid.valid_c27.d = domain_valid_q[27];
  assign hw2reg.domain_division_valid.valid_c28.d = domain_valid_q[28];
  assign hw2reg.domain_division_valid.valid_c29.d = domain_valid_q[29];
  assign hw2reg.domain_division_valid.valid_c30.d = domain_valid_q[30];
  assign hw2reg.domain_division_valid.valid_c31.d = domain_valid_q[31];

  always_ff @(posedge control_clk_i or negedge control_rst_ni) begin
    if (~control_rst_ni) begin
      valid_q        <= '0;
      domain_valid_q <= '0;
      division_q     <= '0;
    end else begin
      valid_q        <= valid_set;
      domain_valid_q <= domain_valid_set;
      for (int i = 0; i < 32; i++) begin
        if (domain_valid_set[i]) division_q[i] <= domain_division[i];
        else if (valid_set[i])   division_q[i] <= clock_division_reg_concat[i];
      end
    end
  end

  ////////////////////////////////////////////////////////////////////////////////
  // Generate the “spike” and data‐enable bits for the reset_register registers //
  ////////////////////////////////////////////////////////////////////////////////

  assign hw2reg.reset_register.reset_c0.d = 1'b0;
  assign hw2reg.reset_register.reset_c1.d = 1'b0;
  assign hw2reg.reset_register.reset_c2.d = 1'b0;
  assign hw2reg.reset_register.reset_c3.d = 1'b0;
  assign hw2reg.reset_register.reset_c4.d = 1'b0;
  assign hw2reg.reset_register.reset_c5.d = 1'b0;
  assign hw2reg.reset_register.reset_c6.d = 1'b0;
  assign hw2reg.reset_register.reset_c7.d = 1'b0;
  assign hw2reg.reset_register.reset_c8.d = 1'b0;
  assign hw2reg.reset_register.reset_c9.d = 1'b0;
  assign hw2reg.reset_register.reset_c10.d = 1'b0;
  assign hw2reg.reset_register.reset_c11.d = 1'b0;
  assign hw2reg.reset_register.reset_c12.d = 1'b0;
  assign hw2reg.reset_register.reset_c13.d = 1'b0;
  assign hw2reg.reset_register.reset_c14.d = 1'b0;
  assign hw2reg.reset_register.reset_c15.d = 1'b0;
  assign hw2reg.reset_register.reset_c16.d = 1'b0;
  assign hw2reg.reset_register.reset_c17.d = 1'b0;
  assign hw2reg.reset_register.reset_c18.d = 1'b0;
  assign hw2reg.reset_register.reset_c19.d = 1'b0;
  assign hw2reg.reset_register.reset_c20.d = 1'b0;
  assign hw2reg.reset_register.reset_c21.d = 1'b0;
  assign hw2reg.reset_register.reset_c22.d = 1'b0;
  assign hw2reg.reset_register.reset_c23.d = 1'b0;
  assign hw2reg.reset_register.reset_c24.d = 1'b0;
  assign hw2reg.reset_register.reset_c25.d = 1'b0;
  assign hw2reg.reset_register.reset_c26.d = 1'b0;
  assign hw2reg.reset_register.reset_c27.d = 1'b0;
  assign hw2reg.reset_register.reset_c28.d = 1'b0;
  assign hw2reg.reset_register.reset_c29.d = 1'b0;
  assign hw2reg.reset_register.reset_c30.d = 1'b0;
  assign hw2reg.reset_register.reset_c31.d = 1'b0;

  assign hw2reg.reset_register.reset_c0.de = reg2hw.reset_register.reset_c0;
  assign hw2reg.reset_register.reset_c1.de = reg2hw.reset_register.reset_c1;
  assign hw2reg.reset_register.reset_c2.de = reg2hw.reset_register.reset_c2;
  assign hw2reg.reset_register.reset_c3.de = reg2hw.reset_register.reset_c3;
  assign hw2reg.reset_register.reset_c4.de = reg2hw.reset_register.reset_c4;
  assign hw2reg.reset_register.reset_c5.de = reg2hw.reset_register.reset_c5;
  assign hw2reg.reset_register.reset_c6.de = reg2hw.reset_register.reset_c6;
  assign hw2reg.reset_register.reset_c7.de = reg2hw.reset_register.reset_c7;
  assign hw2reg.reset_register.reset_c8.de = reg2hw.reset_register.reset_c8;
  assign hw2reg.reset_register.reset_c9.de = reg2hw.reset_register.reset_c9;
  assign hw2reg.reset_register.reset_c10.de = reg2hw.reset_register.reset_c10;
  assign hw2reg.reset_register.reset_c11.de = reg2hw.reset_register.reset_c11;
  assign hw2reg.reset_register.reset_c12.de = reg2hw.reset_register.reset_c12;
  assign hw2reg.reset_register.reset_c13.de = reg2hw.reset_register.reset_c13;
  assign hw2reg.reset_register.reset_c14.de = reg2hw.reset_register.reset_c14;
  assign hw2reg.reset_register.reset_c15.de = reg2hw.reset_register.reset_c15;
  assign hw2reg.reset_register.reset_c16.de = reg2hw.reset_register.reset_c16;
  assign hw2reg.reset_register.reset_c17.de = reg2hw.reset_register.reset_c17;
  assign hw2reg.reset_register.reset_c18.de = reg2hw.reset_register.reset_c18;
  assign hw2reg.reset_register.reset_c19.de = reg2hw.reset_register.reset_c19;
  assign hw2reg.reset_register.reset_c20.de = reg2hw.reset_register.reset_c20;
  assign hw2reg.reset_register.reset_c21.de = reg2hw.reset_register.reset_c21;
  assign hw2reg.reset_register.reset_c22.de = reg2hw.reset_register.reset_c22;
  assign hw2reg.reset_register.reset_c23.de = reg2hw.reset_register.reset_c23;
  assign hw2reg.reset_register.reset_c24.de = reg2hw.reset_register.reset_c24;
  assign hw2reg.reset_register.reset_c25.de = reg2hw.reset_register.reset_c25;
  assign hw2reg.reset_register.reset_c26.de = reg2hw.reset_register.reset_c26;
  assign hw2reg.reset_register.reset_c27.de = reg2hw.reset_register.reset_c27;
  assign hw2reg.reset_register.reset_c28.de = reg2hw.reset_register.reset_c28;
  assign hw2reg.reset_register.reset_c29.de = reg2hw.reset_register.reset_c29;
  assign hw2reg.reset_register.reset_c30.de = reg2hw.reset_register.reset_c30;
  assign hw2reg.reset_register.reset_c31.de = reg2hw.reset_register.reset_c31;

  // Synchronize valid bits into high frequencies
  logic [31:0] clock_division_reg_valid_d1;
  logic [31:0] clock_division_reg_valid_d2;
  always_ff @(posedge mst_clk_after_pll or negedge mst_rst_pipe_d2) begin
    if (~mst_rst_pipe_d2) begin
      clock_division_reg_valid_d1 <= '0;
      clock_division_reg_valid_d2 <= '0;
    end else begin
      clock_division_reg_valid_d1 <= valid_q | domain_valid_q;
      clock_division_reg_valid_d2 <= clock_division_reg_valid_d1;
    end
  end

  // Concatenate all reg value into one vector
  assign clock_division_reg_concat[0]  = reg2hw.clock_division_register_c0_c3.division_c0;
  assign clock_division_reg_concat[1]  = reg2hw.clock_division_register_c0_c3.division_c1;
  assign clock_division_reg_concat[2]  = reg2hw.clock_division_register_c0_c3.division_c2;
  assign clock_division_reg_concat[3]  = reg2hw.clock_division_register_c0_c3.division_c3;
  assign clock_division_reg_concat[4]  = reg2hw.clock_division_register_c4_c7.division_c4;
  assign clock_division_reg_concat[5]  = reg2hw.clock_division_register_c4_c7.division_c5;
  assign clock_division_reg_concat[6]  = reg2hw.clock_division_register_c4_c7.division_c6;
  assign clock_division_reg_concat[7]  = reg2hw.clock_division_register_c4_c7.division_c7;
  assign clock_division_reg_concat[8]  = reg2hw.clock_division_register_c8_c11.division_c8;
  assign clock_division_reg_concat[9]  = reg2hw.clock_division_register_c8_c11.division_c9;
  assign clock_division_reg_concat[10] = reg2hw.clock_division_register_c8_c11.division_c10;
  assign clock_division_reg_concat[11] = reg2hw.clock_division_register_c8_c11.division_c11;
  assign clock_division_reg_concat[12] = reg2hw.clock_division_register_c12_c15.division_c12;
  assign clock_division_reg_concat[13] = reg2hw.clock_division_register_c12_c15.division_c13;
  assign clock_division_reg_concat[14] = reg2hw.clock_division_register_c12_c15.division_c14;
  assign clock_division_reg_concat[15] = reg2hw.clock_division_register_c12_c15.division_c15;
  assign clock_division_reg_concat[16] = reg2hw.clock_division_register_c16_c19.division_c16;
  assign clock_division_reg_concat[17] = reg2hw.clock_division_register_c16_c19.division_c17;
  assign clock_division_reg_concat[18] = reg2hw.clock_division_register_c16_c19.division_c18;
  assign clock_division_reg_concat[19] = reg2hw.clock_division_register_c16_c19.division_c19;
  assign clock_division_reg_concat[20] = reg2hw.clock_division_register_c20_c23.division_c20;
  assign clock_division_reg_concat[21] = reg2hw.clock_division_register_c20_c23.division_c21;
  assign clock_division_reg_concat[22] = reg2hw.clock_division_register_c20_c23.division_c22;
  assign clock_division_reg_concat[23] = reg2hw.clock_division_register_c20_c23.division_c23;
  assign clock_division_reg_concat[24] = reg2hw.clock_division_register_c24_c27.division_c24;
  assign clock_division_reg_concat[25] = reg2hw.clock_division_register_c24_c27.division_c25;
  assign clock_division_reg_concat[26] = reg2hw.clock_division_register_c24_c27.division_c26;
  assign clock_division_reg_concat[27] = reg2hw.clock_division_register_c24_c27.division_c27;
  assign clock_division_reg_concat[28] = reg2hw.clock_division_register_c28_c31.division_c28;
  assign clock_division_reg_concat[29] = reg2hw.clock_division_register_c28_c31.division_c29;
  assign clock_division_reg_concat[30] = reg2hw.clock_division_register_c28_c31.division_c30;
  assign clock_division_reg_concat[31] = reg2hw.clock_division_register_c28_c31.division_c31;

`ifndef SYNTHESIS
  // Simulation only, with +clk_div_log: every division a valid bit takes for a
  // divider (it switches at its next count boundary), and the register set
  bit clk_div_log;
  initial clk_div_log = $test$plusargs("clk_div_log");
  always @(posedge control_clk_i) begin
    if (clk_div_log && control_rst_ni) begin
      for (int i = 0; i < NumClocks; i++) begin
        if (domain_valid_set[i])
          $display("[CLK_DIV] %0t domain=%0d division=%0d from=domain", $time, i, domain_division[i]);
        else if (valid_set[i])
          $display("[CLK_DIV] %0t domain=%0d division=%0d from=packed", $time, i, clock_division_reg_concat[i]);
      end
    end
  end
`endif

  ///////////////////////////////
  //    Clock Division Cell    //
  ///////////////////////////////
  logic [NumClocks-1:0] clocks_after_division;
  generate
    for (genvar i = 0; i < NumClocks; i = i + 1) begin : gen_clock_divider
      hemaia_clock_divider #(
          .MaxDivisionWidth(MaxDivisionWidth),
          .DefaultDivision (DefaultDivision[i])
      ) i_clk_divider (
          .clk_i(mst_clk_after_pll),
          .rst_ni(mst_rst_pipe_d2),
          .test_mode_i(test_mode_i),
          .divisor_i(division_q[i][MaxDivisionWidth-1:0]),
          .divisor_valid_i(clock_division_reg_valid_d2[i]),
          .clk_o(clocks_after_division[i])
      );
    end
  endgenerate

  tc_clk_mux2_hs i_clk_o_mux[NumClocks-1:0] (
      .clk0_i   (clocks_after_division),
      .clk1_i   ({NumClocks{mst_clk_i}}),
      .clk_sel_i({NumClocks{pll_bypass_i}}),
      .clk_o    (clk_o)
  );

  logic clk_obs_after_division;
  hemaia_clock_divider #(
      .MaxDivisionWidth(MaxDivisionWidth),
      .DefaultDivision (128)
  ) i_clk_divider (
      .clk_i(mst_clk_after_pll),
      .rst_ni(mst_rst_pipe_d2),
      .test_mode_i(test_mode_i),
      .divisor_i(8'd128),
      .divisor_valid_i('0),
      .clk_o(clk_obs_after_division)
  );

  assign clk_obs_o = pll_bypass_i ? mst_clk_i : clk_obs_after_division;

  //////////////////////////////
  //    Reset synchronizer    //
  //////////////////////////////
  logic [31:0] async_local_rst;
  assign async_local_rst = {<<{~reg2hw.reset_register}};
  hemaia_reset_controller #(
      .NumReset(NumClocks),
      .ResetDelays(ResetDelays)
  ) i_reset_sync (
      .clk_i(clk_o),
      .async_global_rst_ni(mst_rst_ni),
      .async_local_rst_ni(async_local_rst[NumClocks-1:0]),
      .sync_rst_no(rst_no)
  );

endmodule
