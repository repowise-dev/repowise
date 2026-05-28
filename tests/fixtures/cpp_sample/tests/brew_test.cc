#include <gtest/gtest.h>

#include "coffee/brew.h"
#include "brew_fixture.h"

TEST_F(BrewFixture, BrewsFromBeans) {
  coffee::Beans beans{"kenya", 20};
  coffee::Cup cup = brewer.brew(beans);
  EXPECT_EQ(cup.milliliters, 200);
}
