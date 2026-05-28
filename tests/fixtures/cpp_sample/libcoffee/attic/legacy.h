#pragma once

// PLANTED DEAD: a header that no source file in this fixture includes,
// declaring a class no source file references. The dead-code analyzer
// MUST keep flagging this header as unreachable.
namespace coffee {

class LegacyAbandoned {
 public:
  int do_nothing();
};

}  // namespace coffee
