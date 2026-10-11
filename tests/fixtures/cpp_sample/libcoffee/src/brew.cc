#include "coffee/brew.h"

namespace coffee {

Brewer::Brewer() = default;
Brewer::~Brewer() = default;

Cup Brewer::brew(const Beans& beans) {
  Cup cup;
  cup.milliliters = beans.grams * 10;
  cup.flavor = beans.origin + " roast";
  return cup;
}

}  // namespace coffee
