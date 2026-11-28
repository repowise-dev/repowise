add_library(A1 STATIC
    src/a1.cpp
)
target_include_directories(A1 PUBLIC include)
target_link_libraries(A1 PUBLIC ACommon B1)
