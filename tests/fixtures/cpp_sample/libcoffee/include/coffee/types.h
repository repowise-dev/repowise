#pragma once

#include <string>

namespace coffee {

struct Beans {
  std::string origin;
  int grams = 18;
};

struct Cup {
  int milliliters = 0;
  std::string flavor;
};

}  // namespace coffee
