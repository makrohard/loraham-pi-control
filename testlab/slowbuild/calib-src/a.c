/* Calibration workload part a: fixed code for the compiler to optimise. Never edit it:
 * the workload hash in tests/data/slow-target-builds.toml names these exact bytes. */
#define F(n) unsigned a_f##n(unsigned x) { unsigned r = x ^ n##u; \
    for (unsigned i = 0; i < (n##u % 7u) + 3u; i++) { r = r * 2654435761u + (r >> 13) + i; \
        if (r & 1u) r ^= 0x9e3779b9u; else r += (r << 5); } return r; }
#define F10(n) F(n##0) F(n##1) F(n##2) F(n##3) F(n##4) F(n##5) F(n##6) F(n##7) F(n##8) F(n##9)
#define F100(n) F10(n##0) F10(n##1) F10(n##2) F10(n##3) F10(n##4) F10(n##5) F10(n##6) F10(n##7) F10(n##8) F10(n##9)
F100(1) F100(2) F100(3) F100(4) F100(5) F100(6) F100(7) F100(8) F100(9)
unsigned a_run(unsigned x) {
    return a_f100(x) ^ a_f200(x) ^ a_f300(x) ^ a_f400(x) ^ a_f500(x) ^ a_f600(x) ^ a_f700(x) ^ a_f800(x) ^ a_f900(x);
}
