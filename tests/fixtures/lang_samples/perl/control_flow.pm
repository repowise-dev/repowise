package Sample::ControlFlow;
use strict;
use warnings;

sub classify {
    my ($n) = @_;
    if ($n > 10 && $n < 20) {
        return 'teen';
    } elsif ($n == 0) {
        return 'zero';
    } else {
        return 'other';
    }
}

sub guards {
    my ($self, $x) = @_;
    return unless defined $x;
    die "negative" if $x < 0;
    open(my $fh, '<', $x) or die "cannot open $x";
    return $x ? 'yes' : 'no';
}

sub loops {
    my (@items) = @_;
    for my $i (@items) {
        while ($i > 0) {
            $i--;
        }
    }
    print "$_\n" for @items;
    return scalar @items;
}

1;
