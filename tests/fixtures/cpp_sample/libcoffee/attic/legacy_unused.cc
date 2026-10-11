// PLANTED DEAD: defines a class referenced by nothing in this fixture.
// The dead-code analyzer MUST keep flagging GenuinelyDead and this file
// (no #include of legacy.h, no caller of run_unused).
namespace coffee {

class GenuinelyDead {
 public:
  int do_nothing() { return 0; }
};

int run_unused() {
  GenuinelyDead d;
  return d.do_nothing();
}

}  // namespace coffee
