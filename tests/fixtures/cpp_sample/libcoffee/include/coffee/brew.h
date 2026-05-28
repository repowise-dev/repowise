#pragma once

#include "coffee/types.h"

#if defined(_WIN32) && defined(COFFEE_EXPORT_BUILD)
#  define COFFEE_EXPORT __declspec(dllexport)
#else
#  define COFFEE_EXPORT
#endif

namespace coffee {

class COFFEE_EXPORT Brewer {
 public:
  Brewer();
  ~Brewer();
  Cup brew(const Beans& beans);
};

}  // namespace coffee
