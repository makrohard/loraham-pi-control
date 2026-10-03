/* Calibration workload: links the eight parts so none of them is dropped. */
#include <stdio.h>
unsigned a_run(unsigned);
unsigned b_run(unsigned);
unsigned c_run(unsigned);
unsigned d_run(unsigned);
unsigned e_run(unsigned);
unsigned f_run(unsigned);
unsigned g_run(unsigned);
unsigned h_run(unsigned);

int main(void) {
    printf("%u\n", a_run(1u) ^ b_run(1u) ^ c_run(1u) ^ d_run(1u) ^ e_run(1u) ^ f_run(1u) ^ g_run(1u) ^ h_run(1u));
    return 0;
}
