`timescale 1ps/1fs
module hemaia_pll_wrapper #(
    parameter int USE_VENDOR_PLL = 0
) (
    // Input Clk from external source
    input  logic clk_i,
    // Output PLL clock (4.8 GHz)
    output logic clk_o,
    // Control signals
    input  logic pad_pll_bypass_i,
    input  logic pad_pll_en_i,
    input  logic [1:0] pad_pll_post_div_sel_i,
    output logic pad_pll_lock_o,
    input  logic pll_test_en_i,
    input  logic [2:0] pll_test_sel_i,
    output logic pll_test_out_o
);
  if (USE_VENDOR_PLL) begin
    // In this setup, the USE_VENDOR_PLL should be always zero
    $error("USE_VENDOR_PLL is set to 1, but this design is intended to be used without the vendor PLL. Please set USE_VENDOR_PLL to 0.");
  end else begin
    // If not using the vendor PLL, pass the input clock directly to the output and set control signals to default values
    assign clk_o = clk_i; // Pass the input clock directly to the output
    assign pad_pll_lock_o = 1'b0; // Indicate that the PLL is not present
    assign pll_test_out_o = 1'b0; // Set test output to a default value
  end
endmodule
