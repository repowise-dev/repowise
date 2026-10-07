<?php
use PHPUnit\Framework\TestCase;

class AssertionsTest extends TestCase {
    public function testManyAsserts(): void {
        $result = 42;
        $this->assertEquals(42, $result);
        $this->assertTrue($result > 0);
        $this->assertFalse($result < 0);
        $this->assertNotNull($result);
        $this->assertGreaterThan(10, $result);
    }

    public function testFewAsserts(): void {
        $result = 42;
        $this->assertEquals(42, $result);
        echo "computed: " . $result;
        self::assertNotNull($result);
    }
}
